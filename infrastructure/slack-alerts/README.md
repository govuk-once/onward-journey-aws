# Onward Journey - Error Alerting

Onward Journey sends automated alerts to a configured Slack channel, whenever a AWS service or function fails or logs an error. Messages are routed to Slack via Cloudwatch alarms, Simple Notification Service (SNS) and Amazon Q Developer in chat applications (formerly known as AWS Chatbot). This guide will help you set up  alerting in Slack for Onward Journey. The steps differ depending on whether you need to set up or reconfigure alerts for all users, or just add a new user workspace where it is already set up in other workspaces.

Before you begin, navigate to the infrastructure/slack-alerts directory, and sign into AWS via the GDS shell.

```bash
cd infrastructure/slack-alerts

gds aws once-onwardjourney-development-admin --shell
```

## 1. New account or configuration
This section explains how to set up alerting on a new AWS account or one where alerting hasn't been set up yet, or when you need to reconfigure the client and channel (e.g. to move alerts to a different slack channel or workspace). If you just need to add a user workspace, skip to section 2 (Adding a new user workspace).

### Initialise the configuration

If `slack-alerts.config` doesn't exist in the slack-alerts directory, create it, and paste in the text below. Replace `<your-AWS-account-ID>` with your AWS account ID.

```hcl
region = "eu-west-2"
bucket = "govuk-once-onwardjourney-development-<your-AWS-account-ID>-tfstate"
use_lockfile = true
encrypt = true
key = "shared-infrastructure/slack-alerts.tfstate"
```

then, run:
```bash
terraform init -reconfigure -backend-config="slack-alerts.config"
```

### Create or edit a chat client
Amazon Q chat clients can only be created in the browser, to enable authentication with a Slack workspace. This only needs to be done once per AWS account/slack workspace.

First, configure the Slack workspace and channel:
1. Open Slack and sign into the workspace where you want to receive alerts
2. Cick Tools -> Apps in the left sidebar
3. Check whether Amazon Q Developer is installed. If it isn't, install it.
4. Create a channel or open an existing channel where you wish to receive alerts
5. Type the message `/invite @Amazon Q.` and send it in the channel. If prompted, choose "Invite Them"

Then, configure the Amazon Q client:
1. Sign into the AWS browser console and go to https://console.aws.amazon.com/chatbot/
2. Click "Configured Clients". If your Slack workspace isn't listed, click "Configure new client". You'll be redirected to Slack's authorization page to request permission for Amazon Q Developer in chat applications to access your information
3. Select the workspace you'd like to use from the dropdown, and click "Allow".


<!-- how to set up the channel configuration, apply terraform, get SNS topic ARN -->

## 2. Adding a new user workspace

If you already have alerting set up and only need to add a

<!-- tbc -->
