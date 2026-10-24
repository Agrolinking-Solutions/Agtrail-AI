"""
AgTrail sends a stand-in link when an operation is logged without a photo
(https://cdn.agrolinking.com/uploads/no-image.jpg). There is nothing at that
address, so fetching it, archiving it or asking the model about it can only fail
and be retried. Such an image is recorded as having no photo instead.
"""

import re
from urllib.parse import urlparse

_PLACEHOLDER_NAME = re.compile(r"^no-image\.(jpe?g|png|webp|gif)$", re.IGNORECASE)


def is_placeholder_image_url(url: str | None) -> bool:
    if not url:
        return False
    name = urlparse(url).path.rsplit("/", 1)[-1]
    return bool(_PLACEHOLDER_NAME.match(name))
