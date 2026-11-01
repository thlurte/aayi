"""Unit tests for deployment orchestration and tag normalization."""

from aayi.orchestrator.deployer import Deployer


def test_resolve_image_tag_without_existing_tag():
    assert Deployer.resolve_image_tag("myorg/myapp", tag="v1.0", default_revision="latest") == "myorg/myapp:v1.0"
    assert Deployer.resolve_image_tag("myorg/myapp", tag=None, default_revision="git-sha123") == "myorg/myapp:git-sha123"


def test_resolve_image_tag_prevents_duplicate_tag():
    # Base image that already includes a tag suffix like ':prod'
    assert Deployer.resolve_image_tag("myorg/myapp:prod", tag=None, default_revision="latest") == "myorg/myapp:prod"
    # When explicit tag override is provided
    assert Deployer.resolve_image_tag("myorg/myapp:prod", tag="v2.0", default_revision="latest") == "myorg/myapp:v2.0"
