import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock, ANY
from botocore.exceptions import ClientError

from app.agentcore.orchestrator.orchestrator import check_connection, orchestrator_entrypoint

# Unwrap the bedrock_agentcore decorator to test the raw async generator
entry_func = getattr(orchestrator_entrypoint, "__wrapped__", orchestrator_entrypoint)

# =====================================================================
# TEST CASES
# =====================================================================

def test_check_connection_success(caplog):
    """Test the socket utility logs success when a port is open."""
    with patch("socket.create_connection") as mock_socket:
        check_connection("fake-host.com", 443)
        mock_socket.assert_called_once_with(("fake-host.com", 443), timeout=2)
        assert "✅ Connection to fake-host.com successful" in caplog.text


def test_check_connection_failure(caplog):
    """Test the socket utility logs an error but does not crash if connection fails."""
    with patch("socket.create_connection", side_effect=TimeoutError("Timed out")):
        check_connection("fake-host.com", 443)
        assert "❌ Connection to fake-host.com failed" in caplog.text


@pytest.mark.asyncio
async def test_entrypoint_missing_message_payload():
    """Test the orchestrator fails fast if the user input is missing."""
    event = {"body": json.dumps({"thread_id": "t-123"})}  # No 'message' key

    # Consume the async generator
    results = [chunk async for chunk in entry_func(event)]

    assert len(results) == 1
    response = json.loads(results[0])
    assert response.get("error") == "No 'message' found in request payload"


@pytest.mark.asyncio
@patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
@patch("boto3.client")
@patch("app.agentcore.orchestrator.orchestrator.check_connection")
async def test_websocket_streaming_success(_mock_check_conn, mock_boto, mock_astream):
    """
    Test that tokens yielded by LangGraph are correctly placed into the async queue
    and dispatched to API Gateway via boto3.
    """
    # 1. Setup Mock Event with WebSocket routing metadata
    event = {
        "body": json.dumps({
            "message": "Hello",
            "connection_id": "conn-xyz",
            "domain_name": "api.aws.com",
            "stage": "dev"
        })
    }

    # 2. Setup API Gateway Mock
    mock_apigw = MagicMock()
    mock_boto.return_value = mock_apigw

    # 3. Setup LangGraph Astream Mock (Yielding two token chunks)
    async def fake_stream(*args, **kwargs):
        class MockChunk:
            content = [{"type": "text", "text": "chunk1 "}]
        yield MockChunk(), {"langgraph_node": "chatbot"}

        class MockChunk2:
            content = [{"type": "text", "text": "chunk2"}]
        yield MockChunk2(), {"langgraph_node": "chatbot"}

    mock_astream.side_effect = fake_stream

    # 4. Execute the orchestrator
    async for _ in entry_func(event):
        pass  # We just need it to run to completion

    # 5. Assertions
    mock_boto.assert_called_once_with(
        'apigatewaymanagementapi',
        endpoint_url="https://api.aws.com/dev",
        region_name="eu-west-2",
        config=ANY
    )

    # Verify the queue worker successfully sent both frames plus the 'done' frame
    assert mock_apigw.post_to_connection.call_count == 3
    calls = mock_apigw.post_to_connection.call_args_list

    assert json.loads(calls[0].kwargs["Data"]) == {"type": "chunk", "text": "chunk1 "}
    assert json.loads(calls[1].kwargs["Data"]) == {"type": "chunk", "text": "chunk2"}
    assert json.loads(calls[2].kwargs["Data"]) == {"type": "done"}


@pytest.mark.asyncio
@patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
@patch("boto3.client")
@patch("app.agentcore.orchestrator.orchestrator.check_connection")
async def test_websocket_client_disconnect_aborts_stream(_mock_check_conn, mock_boto, mock_astream):
    """
    Test that if a user closes their browser mid-stream (GoneException),
    the background queue detects it, flags the client as dead, and aborts the LLM stream.
    """
    event = {
        "body": json.dumps({"message": "Hello", "connection_id": "conn-xyz", "domain_name": "api.aws.com", "stage": "dev"})
    }

    # 1. Setup API Gateway to simulate a disconnected client
    mock_apigw = MagicMock()
    mock_boto.return_value = mock_apigw

    error_response = {'Error': {'Code': 'GoneException'}}
    mock_apigw.post_to_connection.side_effect = ClientError(error_response, 'PostToConnection')

    # 2. Setup an infinite LangGraph stream
    stream_iterations = 0
    async def infinite_fake_stream(*args, **kwargs):
        nonlocal stream_iterations
        while True:
            stream_iterations += 1
            class MockChunk:
                content = [{"type": "text", "text": "spam"}]
            yield MockChunk(), {"langgraph_node": "chatbot"}
            await asyncio.sleep(0.01)  # Yield to event loop so worker can process

            if stream_iterations > 50:
                raise TimeoutError("Test failed: Stream was not aborted early")

    mock_astream.side_effect = infinite_fake_stream

    # 3. Execute
    async for _ in entry_func(event):
        pass

    # 4. Assertions
    # The stream should have been killed almost instantly, well before 50 iterations
    assert stream_iterations < 50
    # It attempts to send the first chunk, fails, marks dead, and then the 'done' frame is skipped
    assert mock_apigw.post_to_connection.call_count == 1

@pytest.mark.asyncio
@patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
@patch("app.agentcore.orchestrator.orchestrator.check_connection")
async def test_console_fallback_streaming(_mock_check_conn, mock_astream):
    """
    Test that if WebSocket metadata is missing, the orchestrator bypasses API Gateway
    and safely yields standard chunks (useful for local CLI testing).
    """
    # Event WITHOUT connection_id, domain, or stage
    event = {"body": json.dumps({"message": "Hello"})}

    async def fake_stream(*args, **kwargs):
        class MockChunk:
            content = [{"type": "text", "text": "console_chunk"}]
        yield MockChunk(), {"langgraph_node": "chatbot"}

    mock_astream.side_effect = fake_stream

    # Consume the generator
    results = [chunk async for chunk in entry_func(event)]

    # Assert it yielded directly instead of queueing to boto3
    assert len(results) == 2
    assert json.loads(results[0]) == {"type": "chunk", "text": "console_chunk"}
    assert json.loads(results[1]) == {"type": "done"}

@pytest.mark.asyncio
@patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
@patch("boto3.client")
@patch("app.agentcore.orchestrator.orchestrator.check_connection")
async def test_websocket_non_fatal_error_continues_stream(_mock_check_conn, mock_boto, mock_astream, caplog):
    """
    Test that generic exceptions during a WebSocket push are logged but do NOT
    abort the generator stream like a GoneException does.
    """
    event = {
        "body": json.dumps({"message": "Hello", "connection_id": "conn-xyz", "domain_name": "api.aws.com", "stage": "dev"})
    }

    mock_apigw = MagicMock()
    mock_boto.return_value = mock_apigw

    # Setup boto3 to fail on the FIRST chunk, but succeed on the rest
    mock_apigw.post_to_connection.side_effect = [
        Exception("Temporary network blip"),
        None, # Success for chunk 2
        None  # Success for 'done' frame
    ]

    async def fake_stream(*args, **kwargs):
        class MockChunk1:
            content = [{"type": "text", "text": "chunk1"}]
        yield MockChunk1(), {"langgraph_node": "chatbot"}

        class MockChunk2:
            content = [{"type": "text", "text": "chunk2"}]
        yield MockChunk2(), {"langgraph_node": "chatbot"}

    mock_astream.side_effect = fake_stream

    async for _ in entry_func(event):
        pass

    # Assertions
    # It should have attempted all 3 pushes (chunk1, chunk2, done) despite the first one failing
    assert mock_apigw.post_to_connection.call_count == 3

    # Verify the error was safely caught and logged
    assert "Error pushing to WebSocket: Temporary network blip" in caplog.text
