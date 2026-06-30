resource "aws_sns_topic" "lambda_errors" {
  name = "lambda-errors-topic"
}

data "aws_chatbot_slack_workspace" "gds_oj_slack" {
  slack_team_name = "GDS"
}

resource "aws_chatbot_slack_channel_configuration" "oj_aws_errors" {
  configuration_name = "oj-aws-errors-config"
  iam_role_arn       = aws_iam_role.amazon_q.arn
  slack_channel_id   = "C0BD43H6J6N"
  slack_team_id      = data.aws_chatbot_slack_workspace.gds_oj_slack.slack_team_id
  sns_topic_arns     = [aws_sns_topic.lambda_errors.arn]
}

# Amazon Q Role
# Allows Amazon Q Developer in chat applications to read Cloudwatch logs
resource "aws_iam_role" "amazon_q" {
  name = "amazon_q_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Sid    = ""
        Principal = {
          Service = "chatbot.amazonaws.com"
        }
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "amazon_q_cloudwatch_access" {
  role       = aws_iam_role.amazon_q.name
  policy_arn = "arn:aws:iam::aws:policy/CloudWatchReadOnlyAccess"
}
