import os
import json
import logging
from abc import ABC, abstractmethod
from typing import BinaryIO, Tuple

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from nucllei.config import (
    S3_ACCESS_KEY_ID,
    S3_BUCKET_NAME,
    S3_ENDPOINT_URL,
    S3_KEY_PREFIX,
    S3_REGION_NAME,
    S3_SECRET_ACCESS_KEY,
    S3_USE_ACCELERATE_ENDPOINT,
    S3_ADDRESSING_STYLE,
    GCS_BUCKET_NAME,
    GOOGLE_APPLICATION_CREDENTIALS_JSON,
    AZURE_STORAGE_ENDPOINT,
    AZURE_STORAGE_CONTAINER_NAME,
    AZURE_STORAGE_KEY,
    STORAGE_PROVIDER,
    UPLOAD_DIR,
)
from google.cloud import storage
from google.cloud.exceptions import GoogleCloudError, NotFound
from nucllei.constants import ERROR_MESSAGES
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import ResourceNotFoundError
from nucllei.env import SRC_LOG_LEVELS


log = logging.getLogger(__name__)
log.setLevel(SRC_LOG_LEVELS["MAIN"])


class StorageProvider(ABC):
    @abstractmethod
    def get_file(self, file_path: str) -> str:
        pass

    @abstractmethod
    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        pass

    @abstractmethod
    def delete_file(self, file_path: str) -> None:
        pass


class LocalStorageProvider(StorageProvider):
    @staticmethod
    def upload_file(file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        contents = file.read()
        if not contents:
            raise ValueError(ERROR_MESSAGES.EMPTY_CONTENT)
        file_path = f"{UPLOAD_DIR}/{filename}"
        with open(file_path, "wb") as f:
            f.write(contents)
        return contents, file_path

    @staticmethod
    def get_file(file_path: str) -> str:
        """Handles downloading of the file from local storage."""
        return file_path

    @staticmethod
    def delete_file(file_path: str) -> None:
        """Handles deletion of the file from local storage."""
        filename = file_path.split("/")[-1]
        file_path = f"{UPLOAD_DIR}/{filename}"
        if os.path.isfile(file_path):
            os.remove(file_path)
        else:
            log.warning(f"File {file_path} not found in local storage.")


class S3StorageProvider(StorageProvider):
    def __init__(self):
        config = Config(
            s3={
                "use_accelerate_endpoint": S3_USE_ACCELERATE_ENDPOINT,
                "addressing_style": S3_ADDRESSING_STYLE,
            },
        )

        # If access key and secret are provided, use them for authentication
        if S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY:
            self.s3_client = boto3.client(
                "s3",
                region_name=S3_REGION_NAME,
                endpoint_url=S3_ENDPOINT_URL,
                aws_access_key_id=S3_ACCESS_KEY_ID,
                aws_secret_access_key=S3_SECRET_ACCESS_KEY,
                config=config,
            )
        else:
            # If no explicit credentials are provided, fall back to default AWS credentials
            # This supports workload identity (IAM roles for EC2, EKS, etc.)
            self.s3_client = boto3.client(
                "s3",
                region_name=S3_REGION_NAME,
                endpoint_url=S3_ENDPOINT_URL,
                config=config,
            )

        self.bucket_name = S3_BUCKET_NAME
        self.key_prefix = S3_KEY_PREFIX if S3_KEY_PREFIX else ""

    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        """Handles uploading of the file to S3 storage."""
        _, file_path = LocalStorageProvider.upload_file(file, filename)
        try:
            s3_key = os.path.join(self.key_prefix, filename)
            self.s3_client.upload_file(file_path, self.bucket_name, s3_key)
            return (
                open(file_path, "rb").read(),
                "s3://" + self.bucket_name + "/" + s3_key,
            )
        except ClientError as e:
            raise RuntimeError(f"Error uploading file to S3: {e}")

    def get_file(self, file_path: str) -> str:
        """Handles downloading of the file from S3 storage."""
        try:
            s3_key = self._extract_s3_key(file_path)
            local_file_path = self._get_local_file_path(s3_key)
            self.s3_client.download_file(self.bucket_name, s3_key, local_file_path)
            return local_file_path
        except ClientError as e:
            raise RuntimeError(f"Error downloading file from S3: {e}")

    def delete_file(self, file_path: str) -> None:
        """Handles deletion of the file from S3 storage."""
        try:
            s3_key = self._extract_s3_key(file_path)
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
        except ClientError as e:
            raise RuntimeError(f"Error deleting file from S3: {e}")

        # Always delete from local storage
        LocalStorageProvider.delete_file(file_path)

    # The s3 key is the name assigned to an object. It excludes the bucket name, but includes the internal path and the file name.
    def _extract_s3_key(self, full_file_path: str) -> str:
        return "/".join(full_file_path.split("//")[1].split("/")[1:])

    def _get_local_file_path(self, s3_key: str) -> str:
        return f"{UPLOAD_DIR}/{s3_key.split('/')[-1]}"


class GCSStorageProvider(StorageProvider):
    def __init__(self):
        self.bucket_name = GCS_BUCKET_NAME

        if GOOGLE_APPLICATION_CREDENTIALS_JSON:
            self.gcs_client = storage.Client.from_service_account_info(
                info=json.loads(GOOGLE_APPLICATION_CREDENTIALS_JSON)
            )
        else:
            # if no credentials json is provided, credentials will be picked up from the environment
            # if running on local environment, credentials would be user credentials
            # if running on a Compute Engine instance, credentials would be from Google Metadata server
            self.gcs_client = storage.Client()
        self.bucket = self.gcs_client.bucket(GCS_BUCKET_NAME)

    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        """Handles uploading of the file to GCS storage."""
        contents, file_path = LocalStorageProvider.upload_file(file, filename)
        try:
            blob = self.bucket.blob(filename)
            blob.upload_from_filename(file_path)
            return contents, "gs://" + self.bucket_name + "/" + filename
        except GoogleCloudError as e:
            raise RuntimeError(f"Error uploading file to GCS: {e}")

    def get_file(self, file_path: str) -> str:
        """Handles downloading of the file from GCS storage."""
        try:
            filename = file_path.removeprefix("gs://").split("/")[1]
            local_file_path = f"{UPLOAD_DIR}/{filename}"
            blob = self.bucket.get_blob(filename)
            blob.download_to_filename(local_file_path)

            return local_file_path
        except NotFound as e:
            raise RuntimeError(f"Error downloading file from GCS: {e}")

    def delete_file(self, file_path: str) -> None:
        """Handles deletion of the file from GCS storage."""
        # llens(#94): purge のリトライ(Storage 削除成功 → DB 更新失敗)を通すため、
        # 不存在は Local と同じ warning のみ(エラーにするとリトライが永久に失敗する)
        filename = file_path.removeprefix("gs://").split("/")[1]
        blob = self.bucket.get_blob(filename)
        if blob is None:
            log.warning(f"File {filename} not found in GCS storage.")
        else:
            try:
                blob.delete()
            except NotFound:
                # get_blob と delete の間に消えた競合も不存在と同じ扱い
                log.warning(f"File {filename} not found in GCS storage.")

        # Always delete from local storage
        LocalStorageProvider.delete_file(file_path)


class AzureStorageProvider(StorageProvider):
    def __init__(self):
        self.endpoint = AZURE_STORAGE_ENDPOINT
        self.container_name = AZURE_STORAGE_CONTAINER_NAME
        storage_key = AZURE_STORAGE_KEY

        if storage_key:
            # Configure using the Azure Storage Account Endpoint and Key
            self.blob_service_client = BlobServiceClient(
                account_url=self.endpoint, credential=storage_key
            )
        else:
            # Configure using the Azure Storage Account Endpoint and DefaultAzureCredential
            # If the key is not configured, then the DefaultAzureCredential will be used to support Managed Identity authentication
            self.blob_service_client = BlobServiceClient(
                account_url=self.endpoint, credential=DefaultAzureCredential()
            )
        self.container_client = self.blob_service_client.get_container_client(
            self.container_name
        )

    def upload_file(self, file: BinaryIO, filename: str) -> Tuple[bytes, str]:
        """Handles uploading of the file to Azure Blob Storage."""
        contents, file_path = LocalStorageProvider.upload_file(file, filename)
        try:
            blob_client = self.container_client.get_blob_client(filename)
            blob_client.upload_blob(contents, overwrite=True)
            return contents, f"{self.endpoint}/{self.container_name}/{filename}"
        except Exception as e:
            raise RuntimeError(f"Error uploading file to Azure Blob Storage: {e}")

    def get_file(self, file_path: str) -> str:
        """Handles downloading of the file from Azure Blob Storage."""
        try:
            filename = file_path.split("/")[-1]
            local_file_path = f"{UPLOAD_DIR}/{filename}"
            blob_client = self.container_client.get_blob_client(filename)
            with open(local_file_path, "wb") as download_file:
                download_file.write(blob_client.download_blob().readall())
            return local_file_path
        except ResourceNotFoundError as e:
            raise RuntimeError(f"Error downloading file from Azure Blob Storage: {e}")

    def delete_file(self, file_path: str) -> None:
        """Handles deletion of the file from Azure Blob Storage."""
        # llens(#94): purge のリトライ(Storage 削除成功 → DB 更新失敗)を通すため、
        # 不存在は Local と同じ warning のみ(エラーにするとリトライが永久に失敗する)
        filename = file_path.split("/")[-1]
        try:
            blob_client = self.container_client.get_blob_client(filename)
            blob_client.delete_blob()
        except ResourceNotFoundError:
            log.warning(f"File {filename} not found in Azure Blob Storage.")

        # Always delete from local storage
        LocalStorageProvider.delete_file(file_path)


def get_storage_provider(storage_provider: str):
    if storage_provider == "local":
        Storage = LocalStorageProvider()
    elif storage_provider == "s3":
        Storage = S3StorageProvider()
    elif storage_provider == "gcs":
        Storage = GCSStorageProvider()
    elif storage_provider == "azure":
        Storage = AzureStorageProvider()
    else:
        raise RuntimeError(f"Unsupported storage provider: {storage_provider}")
    return Storage


Storage = get_storage_provider(STORAGE_PROVIDER)
