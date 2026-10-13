import pytest
import json
import asyncio
from unittest.mock import patch, MagicMock, ANY
from botocore.exceptions import ClientError

from app.agentcore.orchestrator.orchestrator import check_connection, orchestrator_entrypoint

# Unwrap decorator to test the raw async generator
entry_func = getattr(orchestrator_entrypoint, "__wrapped__", orchestrator_entrypoint)

def make_chunk(text: str):
    """Helper to simulate LangGraph streaming chunk objects."""
    chunk = MagicMock()
    chunk.content = [{"type": "text", "text": text}]
    return chunk, {"langgraph_node": "chatbot"}

class TestCheckConnection:
    def test_logs_success_when_port_is_open(self, caplog):
        with patch("socket.create_connection") as mock_socket:
            check_connection("fake-host.com", 443)

            mock_socket.assert_called_once_with(("fake-host.com", 443), timeout=2)
            assert "✅ Connection to fake-host.com successful" in caplog.text

    def test_logs_error_without_raising_when_timed_out(self, caplog):
        with patch("socket.create_connection", side_effect=TimeoutError("Timed out")):
            check_connection("fake-host.com", 443)

            assert "❌ Connection to fake-host.com failed" in caplog.text


class TestOrchestratorValidation:
    @pytest.mark.asyncio
    async def test_returns_error_when_message_payload_is_missing(self):
        event = {"body": json.dumps({"thread_id": "t-123"})}

        results = [json.loads(chunk) async for chunk in entry_func(event)]

        assert len(results) == 1
        assert results[0].get("error") == "No 'message' found in request payload"


@pytest.mark.asyncio
@patch("app.agentcore.orchestrator.orchestrator.check_connection")
class TestWebSocketStreaming:
    @pytest.fixture
    def ws_event(self):
        return {
            "body": json.dumps({
                "message": "Hello",
                "connection_id": "conn-xyz",
                "domain_name": "api.aws.com",
                "stage": "dev",
            })
        }

    @patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
    @patch("boto3.client")
    async def test_dispatches_chunks_and_done_signal_to_api_gateway(
        self, mock_boto, mock_astream, _mock_conn, ws_event
    ):
        mock_apigw = MagicMock()
        mock_boto.return_value = mock_apigw

        async def fake_stream(*_, **__):
            yield make_chunk("chunk1 ")
            yield make_chunk("chunk2")

        mock_astream.side_effect = fake_stream

        async for _ in entry_func(ws_event):
            pass

        mock_boto.assert_called_once_with(
            "apigatewaymanagementapi",
            endpoint_url="https://api.aws.com/dev",
            region_name="eu-west-2",
            config=ANY,
        )

        assert mock_apigw.post_to_connection.call_count == 3
        payloads = [
            json.loads(call.kwargs["Data"])
            for call in mock_apigw.post_to_connection.call_args_list
        ]
        assert payloads == [
            {"type": "chunk", "text": "chunk1 "},
            {"type": "chunk", "text": "chunk2"},
            {"type": "done"},
        ]

    @patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
    @patch("boto3.client")
    async def test_aborts_llm_stream_on_client_disconnect(
        self, mock_boto, mock_astream, _mock_conn, ws_event
    ):
        mock_apigw = MagicMock()
        mock_boto.return_value = mock_apigw
        mock_apigw.post_to_connection.side_effect = ClientError(
            {"Error": {"Code": "GoneException"}}, "PostToConnection"
        )

        stream_iterations = 0

        async def infinite_stream(*_, **__):
            nonlocal stream_iterations
            while True:
                stream_iterations += 1
                yield make_chunk("spam")
                await asyncio.sleep(0.01)
                if stream_iterations > 50:
                    raise TimeoutError("Stream failed to abort early")

        mock_astream.side_effect = infinite_stream

        async for _ in entry_func(ws_event):
            pass

        assert stream_iterations < 50
        assert mock_apigw.post_to_connection.call_count == 1

    @patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
    @patch("boto3.client")
    async def test_logs_and_continues_on_non_fatal_push_error(
        self, mock_boto, mock_astream, _mock_conn, ws_event, caplog
    ):
        mock_apigw = MagicMock()
        mock_boto.return_value = mock_apigw
        mock_apigw.post_to_connection.side_effect = [
            Exception("Temporary network blip"),
            None,
            None,
        ]

        async def fake_stream(*_, **__):
            yield make_chunk("chunk1")
            yield make_chunk("chunk2")

        mock_astream.side_effect = fake_stream

        async for _ in entry_func(ws_event):
            pass

        assert mock_apigw.post_to_connection.call_count == 3
        assert "Error pushing to WebSocket: Temporary network blip" in caplog.text

    @patch("app.agentcore.orchestrator.orchestrator.graph_app.astream")
    async def test_falls_back_to_yielding_chunks_when_websocket_metadata_absent(
        self, mock_astream, _mock_conn
    ):
        console_event = {"body": json.dumps({"message": "Hello"})}

        async def fake_stream(*_, **__):
            yield make_chunk("console_chunk")

        mock_astream.side_effect = fake_stream

        results = [json.loads(chunk) async for chunk in entry_func(console_event)]

        assert results == [
            {"type": "chunk", "text": "console_chunk"},
            {"type": "done"},
        ]
