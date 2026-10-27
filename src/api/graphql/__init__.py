import strawberry

from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from src.common.server import global_server


@strawberry.type
class Query:
    @strawberry.field
    def hello(self) -> str:
        return "Hello World"


schema = strawberry.Schema(Query)

graphql_app = GraphQLRouter(schema)

fast_api_app: FastAPI = global_server.get_app()

fast_api_app.include_router(graphql_app, prefix="/graphql")
