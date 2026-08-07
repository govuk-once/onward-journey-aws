from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

@app.entrypoint
def main_handler(event):
    """
    AgentCore Hello World Entrypoint.
    Provides a simple response to validate infrastructure and VPC deployment
    via the AWS Console before we hook up LangGraph and API Gateway.
    """
    return {
        "status": "success",
        "message": "Hello World from the Amazon Bedrock AgentCore Runtime",
        "echo_event": event
    }

app.run()
