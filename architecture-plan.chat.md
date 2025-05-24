# Backend Architecture Plan for Pocket Alternative

## Initial Discussion

**User**: Lets talk through this, what are some ideas for how this should work. The browser extension and app are relatively simple, what about the backend?

**Assistant**: 

## Architecture Overview
1. **API Service**: A lightweight REST API to handle requests from both the browser extension and Android app
2. **Database**: To store links, tags, and article content
3. **Search Functionality**: For querying across tags and content
4. **Authentication**: Simple auth system since multi-tenancy isn't required

## Specific Implementation Ideas

### Backend Service
- **Serverless Architecture**: AWS Lambda functions to stay within free tier
- **API Gateway**: To route requests to appropriate Lambda functions
- **Simple Authentication**: JWT tokens for the single account

### Database Options
- **DynamoDB**: Good free tier, works well with Lambda
  - Main table for links with GSIs for tag-based queries
  - Could use a single-table design with appropriate partition/sort keys
- **S3**: For storing full article content if implemented

### Article Processing
- **Lambda Function**: Triggered when a new link is saved
  - Extract article content using libraries like Readability
  - Store full text in database or S3
  - Generate tags automatically (optional)
  - Create archive version (for paywall bypass)

### Search Implementation
- **Basic**: DynamoDB queries with filter expressions
- **Advanced**: AWS OpenSearch (formerly Elasticsearch) if budget allows
- **AI-Enhanced**: Lambda function to generate embeddings + vector search
  - Could use Amazon Bedrock or a simpler embedding service

### Monitoring
- **CloudWatch**: Basic monitoring within free tier
- **Health Check Lambda**: Periodic function to verify system health

### CI/CD
- **GitHub Actions**: For automated testing and deployment
- **AWS SAM/CDK**: For infrastructure as code

**User**: How should I think about the pricing of OpenSearch?

**Assistant**:

# AWS OpenSearch Pricing Considerations

AWS OpenSearch (formerly Elasticsearch Service) can be expensive compared to other AWS services, especially for a personal project trying to stay within free tiers. Here's what to consider:

## Key Pricing Factors
- **Instance Types**: The smallest instance (t3.small.search) costs around $25-30/month
- **Storage**: You pay for EBS storage attached to instances (typically $0.10/GB/month)
- **Data Transfer**: Costs for data moving in/out of the service
- **No Free Tier**: Unlike many AWS services, OpenSearch doesn't have a perpetual free tier

## Cost-Saving Alternatives
For your use case with limited scale requirements:

1. **DynamoDB + Full-Text Search**: Use DynamoDB's contains() operations for basic text search
2. **Elasticsearch on EC2**: Run a small Elasticsearch instance yourself on a t2.micro (free tier)
3. **Meilisearch/Typesense**: Lightweight search engines you could self-host
4. **Algolia**: Has a free tier that might be sufficient for personal use
5. **PostgreSQL**: Using its full-text search capabilities if you're already using a relational DB

If you really want the AI-enabled search via embeddings, you might consider:
- **Pinecone**: Has a starter (free) tier
- **Qdrant**: Open-source vector DB with reasonable self-hosting requirements
- **pgvector**: PostgreSQL extension for vector similarity search

For your personal use case, OpenSearch is likely overkill unless you specifically need its advanced features.

**User**: I suppose if I implement AI-enhanced search based on embeddings, opensearch will be unnecessary, right?

**Assistant**:

Yes, that's correct. If you implement AI-enhanced search using embeddings, OpenSearch would be unnecessary for your use case.

With an embedding-based approach:
- You'd generate vector embeddings for your articles/links
- Store these vectors in a vector database or even in a simpler database that supports vector operations
- Search would be done via vector similarity rather than traditional text search

For a personal project with limited scale, you could use:
- **DynamoDB** with a binary attribute type to store embeddings
- **PostgreSQL with pgvector** extension
- A lightweight vector database like **Qdrant** or **Chroma**
- **Pinecone's** free tier

This approach would give you more advanced semantic search capabilities than OpenSearch would provide for basic keyword matching, and likely at a lower cost for your scale requirements.
