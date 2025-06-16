## Getting Started

### Prerequisites

-   Python 3.8+
-   `pip` (Python package installer)

### Installation

1.  **Clone the repository:**

    ```bash
    git clone [https://github.com/your-username/rate-limiter-service.git](https://github.com/your-username/rate-limiter-service.git)
    cd rate-limiter-service
    ```

2.  **Create a virtual environment (recommended):**

    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows: .\venv\Scripts\activate
    ```

3.  **Install dependencies:**

    ```bash
    pip install -r requirements.txt
    ```

### Running the Service

1.  **Run the FastAPI application using Uvicorn:**

    ```bash
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload
    ```
    (The `--reload` flag is useful for development; remove it for production.)

    The service will be accessible at `http://localhost:8000`.

### Accessing API Documentation (Swagger UI)

Once the service is running, you can access the interactive API documentation at:
`http://localhost:8000/docs`

## API Endpoints

### 1. Check and Consume Rate Limit

-   **URL**: `/check_and_consume`
-   **Method**: `POST`
-   **Description**: Checks if a request is allowed based on the defined rate limits and consumes a token if allowed. If the global concurrency limit is reached, the request might be queued.
-   **Request Body**:
    ```json
    {
      "tenant_id": "string",         // Identifier for the tenant (e.g., "my_company")
      "client_id": "string",         // Identifier for the client (e.g., "user_id_123", "api_key_xyz")
      "action_type": "string",       // Type of action being performed (e.g., "login", "search", "upload")
      "max_requests": 10,            // Maximum requests allowed within the window
      "window_duration_seconds": 60  // Duration of the sliding window in seconds
    }
    ```
-   **Response (200 OK)**:
    ```json
    {
      "allowed": true,               // true if the request is allowed, false otherwise
      "remaining_requests": 9,       // Number of remaining requests in the current window
      "reset_time_seconds": 1678886400, // Unix timestamp when the limit resets (approximate)
      "status": "processed"          // "processed" if handled immediately, "rejected" if queue full or timeout
    }
    ```

### 2. Get Rate Limit Status

-   **URL**: `/status/{tenant_id}/{client_id}/{action_type}`
-   **Method**: `GET`
-   **Description**: Retrieves the current rate limit status and recorded timestamps for a specific tenant, client, and action type. Useful for debugging.
-   **Path Parameters**:
    -   `tenant_id` (string)
    -   `client_id` (string)
    -   `action_type` (string)
-   **Response (200 OK)**:
    ```json
    {
      "tenant_id": "string",
      "client_id": "string",
      "action_type": "string",
      "current_count": 5,            // Number of requests in the current window
      "max_requests": 0,             // Note: Max requests not stored per status for now
      "window_duration_seconds": 0,  // Note: Window duration not stored per status for now
      "recorded_timestamps": [1678886300.123, 1678886305.456, ...], // Timestamps of recorded requests
      "queue_length": 0,             // Number of requests currently queued for this tenant
      "next_reset_time": 1678886400  // Approximate Unix timestamp of next potential reset
    }
    ```
-   **Response (404 Not Found)**:
    ```json
    {
      "detail": "Rate limit status not found"
    }
    ```

### 3. Health Check

-   **URL**: `/health`
-   **Method**: `GET`
-   **Description**: Simple endpoint to check if the service is running.
-   **Response (200 OK)**:
    ```json
    {
      "status": "healthy",
      "timestamp": 1678886400.123,
      "global_load": 5,             // Current global concurrent requests
      "max_global_concurrent": 100  // Maximum global concurrent requests allowed
    }
    ```

### 4. Metrics

-   **URL**: `/metrics`
-   **Method**: `GET`
-   **Description**: Provides real-time operational metrics for monitoring the rate limiter's performance.
-   **Response (200 OK)**:
    ```json
    {
      "global_concurrent_requests": 5,      // Current number of globally concurrent requests
      "max_global_concurrent": 100,         // Configured maximum global concurrent requests
      "total_queued_requests": 10,          // Total requests across all tenant queues
      "tenant_concurrent_counts": {         // Concurrent requests per tenant
        "tenant1": 2,
        "tenant2": 3
      },
      "active_tenants": 2,                  // Number of tenants with active concurrent requests
      "timestamp": 1678886400.123
    }
    ```

## Testing

To run the tests, navigate to the project root directory and execute pytest:

```bash
pytest tests/
