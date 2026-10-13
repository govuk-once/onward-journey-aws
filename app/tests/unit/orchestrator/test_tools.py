import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock

from app.agentcore.orchestrator.tools import (
    VPCETransport,
    query_department_database,
    crm_live_chat_tools,
)


# =====================================================================
# Transport Layer
# =====================================================================

class TestVPCETransport:
    @pytest.mark.asyncio
    async def test_rewrites_target_host_while_preserving_canonical_host_header(self):
        transport = VPCETransport(vpce_host="vpce.mock.aws", canonical_host="canonical.mock.aws")
        request = httpx.Request("POST", "https://canonical.mock.aws/mcp")

        with patch("httpx.AsyncHTTPTransport.handle_async_request", new_callable=AsyncMock) as mock_super:
            mock_super.return_value = httpx.Response(200)
            await transport.handle_async_request(request)

        assert request.headers.get("Host") == "canonical.mock.aws"
        assert request.url.host == "vpce.mock.aws"


# =====================================================================
# MCP Gateway Tools
# =====================================================================

@pytest.mark.asyncio
class TestMCPTools:
    @pytest.fixture
    def mock_mcp_session(self):
        """Encapsulates nested async context managers for MCP client and session."""
        session = AsyncMock()
        stream_cm = AsyncMock()
        stream_cm.__aenter__.return_value = (AsyncMock(), AsyncMock(), AsyncMock())

        with (
            patch("app.agentcore.orchestrator.tools.ClientSession") as mock_session_cls,
            patch("app.agentcore.orchestrator.tools.aws_iam_streamablehttp_client", return_value=stream_cm),
        ):
            mock_session_cls.return_value.__aenter__.return_value = session
            yield session

    async def test_crm_live_chat_forwards_runnable_config_to_mcp_gateway(self, mock_mcp_session):
        mock_mcp_session.call_tool.return_value = MagicMock(
            isError=False,
            content=[MagicMock(text="Live chat available")],
        )

        tool_args = {
            "method": "check_chat_availability",
            "live_chat_identifier": "hmrc-taxes",
            "reason": "Complex question",
            "summary": "User needs help with tax bracket.",
        }
        config = {
            "configurable": {
                "thread_id": "thread-abc-123",
                "actor_id": "actor-xyz-987",
            }
        }

        result = await crm_live_chat_tools.ainvoke(tool_args, config=config)

        assert result == "Live chat available"
        mock_mcp_session.call_tool.assert_called_once()
        call_name, call_kwargs = mock_mcp_session.call_tool.call_args[0][0], mock_mcp_session.call_tool.call_args[1]["arguments"]

        assert call_name == "unit-crm-availability___check_chat_availability"
        assert call_kwargs["thread_id"] == "thread-abc-123"
        assert call_kwargs["actor_id"] == "actor-xyz-987"
        assert call_kwargs["method"] == "check_chat_availability"

    async def test_returns_error_string_when_mcp_gateway_fails(self, mock_mcp_session):
        mock_mcp_session.call_tool.side_effect = Exception("Connection timed out")

        result = await query_department_database.ainvoke(
            {"query": "passports"}, config={"configurable": {}}
        )

        assert result == "ERROR: Gateway call failed: Connection timed out"
