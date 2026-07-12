"""Public construction and adapter API for MongoDB post persistence."""

from telegram_assist_bot.infrastructure.persistence.mongodb.approval_repository import (
    MongoApprovalRepository,
    initialize_approval_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
    MINIMUM_MONGODB_WIRE_VERSION,
    POSTS_COLLECTION_NAME,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
    verify_mongodb_connection,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.errors import (
    InvalidPostDocumentError,
    MongoConnectionError,
    MongoIndexInitializationError,
    MongoPersistenceError,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.indexes import (
    POST_EXPIRATION_INDEX_NAME,
    POST_INDEX_SPECS,
    POST_SOURCE_IDENTITY_INDEX_NAME,
    PostIndexSpec,
    initialize_post_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.post_mapper import (
    POST_DOCUMENT_SCHEMA_VERSION,
    post_from_document,
    post_to_document,
    status_transition_to_document,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.post_repository import (
    MongoPostRepository,
)

__all__ = (
    "MINIMUM_MONGODB_WIRE_VERSION",
    "POSTS_COLLECTION_NAME",
    "POST_DOCUMENT_SCHEMA_VERSION",
    "POST_EXPIRATION_INDEX_NAME",
    "POST_INDEX_SPECS",
    "POST_SOURCE_IDENTITY_INDEX_NAME",
    "InvalidPostDocumentError",
    "MongoApprovalRepository",
    "MongoConnectionError",
    "MongoIndexInitializationError",
    "MongoPersistenceError",
    "MongoPostRepository",
    "PostIndexSpec",
    "close_mongodb_client",
    "create_mongodb_client",
    "get_posts_collection",
    "initialize_approval_indexes",
    "initialize_post_indexes",
    "post_from_document",
    "post_to_document",
    "status_transition_to_document",
    "verify_mongodb_connection",
)
