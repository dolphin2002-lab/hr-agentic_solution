"""Automated ingestion pipeline for ALTOSTRAT SINGAPORE EMPLOYEE POLICY HANDBOOK & CONDUCT GUIDELINES into Vertex AI Search (VAIS)."""

import os
import subprocess
from pathlib import Path
from typing import Optional

from google.cloud import discoveryengine_v1
from google.cloud import storage


POLICY_DOC_ID = "1ZyCYGkrmzJre94Etl8lJHJSBRaql2FZYidB432EMP-s"
LOCAL_POLICY_PATH = Path(__file__).resolve().parent.parent / "data" / "policies" / "altostrat_singapore_policy_handbook.md"


def export_google_doc_to_local(doc_id: str = POLICY_DOC_ID, output_path: Path = LOCAL_POLICY_PATH) -> Path:
  """Exports the Google Doc policy handbook to local markdown via gdocs CLI if available."""
  output_path.parent.mkdir(parents=True, exist_ok=True)
  gdocs_bin = "/google/bin/releases/gemini-agents-gdocs/gdocs"
  if Path(gdocs_bin).exists():
    result = subprocess.run(
        [gdocs_bin, "readonly", "read", doc_id, "--comments=false"],
        capture_output=True,
        text=True,
        check=True,
    )
    output_path.write_text(result.stdout, encoding="utf-8")
  return output_path


def upload_to_gcs(local_path: Path, bucket_name: str, destination_blob_name: str) -> str:
  """Uploads the exported policy handbook to Google Cloud Storage."""
  storage_client = storage.Client()
  bucket = storage_client.bucket(bucket_name)
  blob = bucket.blob(destination_blob_name)
  blob.upload_from_filename(str(local_path))
  return f"gs://{bucket_name}/{destination_blob_name}"


def import_to_vertex_ai_search(
    project_id: str,
    location: str,
    data_store_id: str,
    gcs_uri: str,
) -> str:
  """Triggers Discovery Engine API import_documents into Vertex AI Search Data Store."""
  client = discoveryengine_v1.DocumentServiceClient()
  parent = client.branch_path(
      project=project_id,
      location=location,
      data_store=data_store_id,
      branch="default_branch",
  )
  gcs_source = discoveryengine_v1.GcsSource(
      input_uris=[gcs_uri],
      data_schema="content",
  )
  request = discoveryengine_v1.ImportDocumentsRequest(
      parent=parent,
      gcs_source=gcs_source,
      reconciliation_mode=discoveryengine_v1.ImportDocumentsRequest.ReconciliationMode.INCREMENTAL,
  )
  operation = client.import_documents(request=request)
  return operation.operation.name


def main() -> None:
  project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "altostrat-hr-agent-prod")
  location = os.environ.get("VAIS_LOCATION", "global")
  data_store_id = os.environ.get("VAIS_DATASTORE_ID", "hr-policy-datastore")
  bucket_name = os.environ.get("HR_POLICY_GCS_BUCKET", f"{project_id}-hr-policies")

  print(f"[1/3] Exporting Google Doc ({POLICY_DOC_ID}) to {LOCAL_POLICY_PATH}...")
  local_file = export_google_doc_to_local()
  print(f"      Saved {local_file.stat().st_size} bytes locally.")

  if os.environ.get("ENABLE_CLOUD_VAIS_UPLOAD", "false").lower() == "true":
    print(f"[2/3] Uploading to GCS bucket gs://{bucket_name}/...")
    gcs_uri = upload_to_gcs(
        local_file,
        bucket_name=bucket_name,
        destination_blob_name="policies/altostrat_singapore_policy_handbook.md",
    )
    print(f"[3/3] Triggering Vertex AI Search Data Store ({data_store_id}) import from {gcs_uri}...")
    op_name = import_to_vertex_ai_search(
        project_id=project_id,
        location=location,
        data_store_id=data_store_id,
        gcs_uri=gcs_uri,
    )
    print(f"      Import Operation started: {op_name}")
  else:
    print("[2/3 & 3/3] Local fallback ready. Set ENABLE_CLOUD_VAIS_UPLOAD=true to push to GCP GCS & VAIS.")


if __name__ == "__main__":
  main()
