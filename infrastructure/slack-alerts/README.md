# Onward Journey - Error Alerting

Onward Journey sends automated alerts to a configured Slack channel, whenever an AWS lambda function fails or logs an error. Messages are routed to Slack via Cloudwatch alarms, Simple Notification Service (SNS) and Amazon Q Developer in chat applications (formerly known as AWS Chatbot). This guide will help you set up  alerting in Slack for Onward Journey. The steps differ depending on whether you need to set up or reconfigure alerts for all users, or just add a new developer with their own Terraform workspace.


## Section 1: New account or configuration

This is a **one-time step**  to be completed only the first time you deploy Onward Journey to a new AWS account, or with a new Slack workspace or channel. To add a developer to an **existing account**, skip to section 2.

### Create or edit a chat client
Amazon Q chat clients can only be created in the browser, to enable authentication with a Slack workspace. This only needs to be done once per AWS account/Slack workspace.

First, configure the Slack workspace and channel:
1. Open Slack and sign into the workspace where you want to receive alerts
2. Cick Tools -> Apps in the left sidebar
3. Check whether Amazon Q Developer is installed. If it isn't, install it.
4. Create a channel or open an existing channel where you wish to receive alerts
5. Open the channel and type the message `/invite @Amazon Q.` and send it in the channel. If prompted, choose "Invite Them"

Then, configure the Amazon Q client:
1. Sign into the AWS browser console and go to https://console.aws.amazon.com/chatbot/
2. Click "Configured Clients". If your Slack workspace isn't listed, click "Configure new client". You'll be redirected to Slack's authorization page to request permission for Amazon Q Developer in chat applications to access your information
3. Select the workspace you'd like to use from the dropdown, and click "Allow".

### Initialise and apply the terraform configuration

Navigate to the infrastructure/slack-alerts directory, and sign into AWS via the GDS shell.

```bash
cd infrastructure/slack-alerts

gds aws once-onwardjourney-development-admin --shell
```

If `slack-alerts.config` doesn't exist in the slack-alerts directory, create it, and paste in the text below. Replace `<your-AWS-account-ID>` with your AWS account ID.

```hcl
region = "eu-west-2"
bucket = "govuk-once-onwardjourney-development-<your-AWS-account-ID>-tfstate"
use_lockfile = true
encrypt = true
key = "shared-infrastructure/slack-alerts.tfstate"
```
Ensure that you are using the default Terraform workspace with
 ```bash
 terraform workspace list
 ```
 You should see `*default` - note the asterisk(*)

Then, run:
```bash
terraform init -reconfigure -backend-config="slack-alerts.config"
```


Set up Terraform variables:
1. Get the channel ID and workspace ID for the Slack channel/workspace you configured earlier (see https://slack.com/intl/en-gb/help/articles/221769328-Locate-your-Slack-URL-or-ID for instructions on how to get these IDs)
2. Update `infrastructure/slack-alerts/local.auto.tfvars` with the following (if it doesn't exist, create it):
```hcl
slack_channel_id = "<your Slack channel ID>"
slack_workspace_id = "<your Slack workspace ID>"
```

After that, run:

```bash
terraform apply
```
Type `yes` when prompted to save the configuration. Go to Section 2 to set up alerting for your Terraform workspace.


## Section 2: Adding a new Terraform developer workspace

Follow these instructions if you already have a Slack workspace and channel configured for receiving error alerts, and you need to set up a new developer workspace.

Navigate to `infrastructure/services`. If you haven't already, authenticate using the GDS AWS shell, and get the SNS topic ARN for your error alerts:

```bash
# change working directory
cd infrastructure/services

# authenticate if you haven't already
gds aws once-onwardjourney-development-admin --shell

# output the SNS topic ARN to the console and copy it to the clipboard
aws sns list-topics --query "Topics[?ends_with(TopicArn, ':oj-aws-errors')].TopicArn" --output text | sed 's/.*/"&"/'
```

in `local.auto.tfvars`, update the value for `sns_topic_arn` with the ARN you just copied. Ensure it is wrapped in double quotes (""). It should look like this, with `<your AWS account>` replaced with your AWS account ID:
```hcl
sns_topic_arn = "arn:aws:sns:eu-west-2:<your AWS account>:oj-aws-errors"
```

**Note:** For the above step, ensure you are editing the version of `local.auto.tfvars` in the correct directory (`infrastructure/services`, not `infrastructure/slack-alerts`)

Before applying, check that you are in your Terraform developer workspace by running `terraform workspace list`. If you don't see an asterisk(*) next to your workspace name, switch into your workspace with:
```bash
terraform workspace select <workspace_name>
```
Finally, run:
```bash
terraform apply
```
Alerting should now be set up, and you should receive a message in the configured Slack channel if any errors or failures occur in any of the lambda functions in your Terraform workspace.

## Section 3: Adding new functions and resources

Error alerting is set up to monitor log groups for all existing lambda functions and step functions. If any new functions or resources are created, or if any existing functions are renamed, they won't trigger alerts automatically.

To monitor a new or renamed resource and turn on error alerts:
- Sign into the AWS browser console and navigate to the Cloudwatch dashboard, then click **Log Management** under **Logs**.
- find the log group for the resource you want to monitor
- copy the log group name, e.g. `/aws/lambda/myworkspace-orchestrator`
- in the terminal on your local machine, navigate to `infrastructure/services`
- open `alerting.tf` in a text editor
- add the log group name to the `main_log_groups` list inside the `locals` block, wrapped in quotes ("")
- replace your Terraform workspace name with `${var.environment}`. E.g. `/aws/lambda/myworkspace-orchestrator` becomes `/aws/lambda/${var.environment}-orchestrator`
- run `terraform apply` and type `yes` to accept the changes

Alerting should now be set up for the new log group that you have added.
