import boto3
from dagster import ConfigurableResource, EnvVar

from fpl_ingestion.storage import S3Store


class StoreResource(ConfigurableResource):
    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str

    def build(self) -> S3Store:
        return S3Store(
            boto3.client(
                "s3",
                endpoint_url=self.endpoint_url,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
            ),
            self.bucket,
        )


STORE = StoreResource(
    endpoint_url=EnvVar("S3_ENDPOINT_URL"),
    access_key_id=EnvVar("S3_ACCESS_KEY_ID"),
    secret_access_key=EnvVar("S3_SECRET_ACCESS_KEY"),
    bucket=EnvVar("S3_BUCKET"),
)
