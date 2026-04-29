locals {
  # Finds all files within the '/mock_data' folder and creates a list of file paths (e.g., 'mock_rag_data.csv') for Terraform to track.
  mock_data_files = fileset("${path.module}/mock_data", "**")

  # Load and decode the pipelines YAML file
  raw_pipeline_config = yamldecode(file("${path.module}/pipelines.yaml"))

  # Filter only the enabled pipelines and convert to a map for the for_each loop
  active_pipelines = {
    for pipeline in local.raw_pipeline_config.pipelines :
    pipeline.name => pipeline if pipeline.enabled
  }
}
