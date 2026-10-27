from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

app = FastAPI()

graphql_router = GraphQLRouter(schema=None, path="/")  # Replace `None` with your actual schema

app.include_router(graphql_router, prefix="/graphql", tags=["GraphQL"])
