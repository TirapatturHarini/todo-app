#!/bin/bash

# Test script to verify todo application logging and trace correlation
# Run this after deploying the updated OpenTelemetry collector config

API_HOST=${API_HOST:-"localhost:8080"}
GRAFANA_HOST=${GRAFANA_HOST:-"localhost:3001"}

echo "=== Todo Application Logging Test ==="
echo "Testing API at: $API_HOST"
echo "Grafana at: $GRAFANA_HOST"
echo ""

# Function to extract trace ID from response headers
extract_trace_id() {
    local response_file=$1
    grep -i "x-trace-id:" $response_file | cut -d: -f2 | tr -d ' \r\n'
}

# Function to test todo operations and collect trace IDs
test_todo_operations() {
    echo "1. Testing Health Check..."
    curl -s -D health_headers.txt -o health_response.json http://$API_HOST/health
    health_trace_id=$(extract_trace_id health_headers.txt)
    echo "   Health check trace ID: $health_trace_id"
    echo ""

    echo "2. Creating a new todo..."
    curl -s -D create_headers.txt -o create_response.json \
        -H "Content-Type: application/json" \
        -X POST \
        -d '{"title": "Test Todo for Tracing", "description": "Testing trace correlation"}' \
        http://$API_HOST/todos
    
    create_trace_id=$(extract_trace_id create_headers.txt)
    todo_id=$(cat create_response.json | python3 -c "import sys, json; print(json.load(sys.stdin).get('id', 'unknown'))")
    echo "   Create todo trace ID: $create_trace_id"
    echo "   Created todo ID: $todo_id"
    echo ""

    if [ "$todo_id" != "unknown" ] && [ "$todo_id" != "" ]; then
        echo "3. Getting the todo..."
        curl -s -D get_headers.txt -o get_response.json http://$API_HOST/todos/$todo_id
        get_trace_id=$(extract_trace_id get_headers.txt)
        echo "   Get todo trace ID: $get_trace_id"
        echo ""

        echo "4. Updating the todo..."
        curl -s -D update_headers.txt -o update_response.json \
            -H "Content-Type: application/json" \
            -X PUT \
            -d '{"title": "Updated Test Todo", "completed": false}' \
            http://$API_HOST/todos/$todo_id
        
        update_trace_id=$(extract_trace_id update_headers.txt)
        echo "   Update todo trace ID: $update_trace_id"
        echo ""

        echo "5. Completing the todo..."
        curl -s -D complete_headers.txt -o complete_response.json \
            -X POST \
            http://$API_HOST/todos/$todo_id/complete
        
        complete_trace_id=$(extract_trace_id complete_headers.txt)
        echo "   Complete todo trace ID: $complete_trace_id"
        echo ""

        echo "6. Getting trace info..."
        curl -s -D trace_info_headers.txt -o trace_info_response.json \
            http://$API_HOST/todos/$todo_id/trace
        
        trace_info_trace_id=$(extract_trace_id trace_info_headers.txt)
        echo "   Trace info trace ID: $trace_info_trace_id"
        echo ""

        echo "7. Deleting the todo..."
        curl -s -D delete_headers.txt -o delete_response.json \
            -X DELETE \
            http://$API_HOST/todos/$todo_id
        
        delete_trace_id=$(extract_trace_id delete_headers.txt)
        echo "   Delete todo trace ID: $delete_trace_id"
        echo ""
    else
        echo "   Skipping remaining tests - todo creation failed"
    fi
}

# Function to check logs in container
check_container_logs() {
    echo "=== Checking Application Container Logs ==="
    
    # Find todo app pods
    todo_pods=$(kubectl get pods -o name | grep -i todo | head -1)
    
    if [ -n "$todo_pods" ]; then
        echo "Found todo pod: $todo_pods"
        echo "Recent logs from todo application:"
        kubectl logs $todo_pods --tail=20 | grep -E "(trace_id|todo|TODO|Creating|Updating|Deleting|Completing)"
    else
        echo "No todo pods found. Checking all pods for todo-related logs..."
        kubectl get pods -o name | while read pod; do
            if kubectl logs $pod --tail=10 2>/dev/null | grep -q -i todo; then
                echo "Found todo logs in $pod:"
                kubectl logs $pod --tail=5 | grep -i todo
            fi
        done
    fi
    echo ""
}

# Function to generate Loki queries
generate_loki_queries() {
    echo "=== Loki Query Examples ==="
    echo "Use these queries in Grafana -> Loki to find your application logs:"
    echo ""
    echo "1. All todo application logs:"
    echo "   {operation=~\".*todo.*\"}"
    echo ""
    echo "2. Logs with trace IDs:"
    echo "   {level=\"info\"} |~ \"trace_id=[a-f0-9]{32}\""
    echo ""
    echo "3. Todo operations by type:"
    echo "   {operation=\"create_todo\"}"
    echo "   {operation=\"update_todo\"}"
    echo "   {operation=\"complete_todo\"}"
    echo "   {operation=\"delete_todo\"}"
    echo ""
    echo "4. Business status filtering:"
    echo "   {business_status=\"created todo successfully\"}"
    echo "   {business_status=\"completed\"}"
    echo ""
    echo "5. Recent todo operations (last 5 minutes):"
    echo "   {service_name=\"todo-api\"} |~ \"todo\" | json"
    echo ""
}

# Function to check OpenTelemetry collector status
check_otel_collector() {
    echo "=== OpenTelemetry Collector Status ==="
    
    # Check if collector is running
    collector_pod=$(kubectl get pods -n observability -o name | grep otel-collector | head -1)
    
    if [ -n "$collector_pod" ]; then
        echo "Collector pod: $collector_pod"
        echo "Collector status:"
        kubectl get $collector_pod -n observability
        echo ""
        
        echo "Recent collector logs (looking for errors):"
        kubectl logs $collector_pod -n observability --tail=10 | grep -E "(ERROR|error|failed|Failed)"
        echo ""
        
        echo "Collector metrics endpoint (should be accessible):"
        kubectl port-forward $collector_pod -n observability 8888:8888 &
        sleep 2
        curl -s http://localhost:8888/metrics | grep -E "(otelcol_receiver|otelcol_exporter)" | head -5
        pkill -f "port-forward.*8888:8888"
        echo ""
    else
        echo "OpenTelemetry collector not found in observability namespace"
    fi
}

# Main execution
echo "Starting todo application logging test..."
echo "Timestamp: $(date)"
echo ""

# Test the todo operations
test_todo_operations

# Check container logs
check_container_logs

# Check OpenTelemetry collector
check_otel_collector

# Generate helpful queries
generate_loki_queries

echo "=== Test Summary ==="
echo "Trace IDs collected:"
[ -n "$health_trace_id" ] && echo "  Health: $health_trace_id"
[ -n "$create_trace_id" ] && echo "  Create: $create_trace_id"
[ -n "$get_trace_id" ] && echo "  Get: $get_trace_id"
[ -n "$update_trace_id" ] && echo "  Update: $update_trace_id"
[ -n "$complete_trace_id" ] && echo "  Complete: $complete_trace_id"
[ -n "$delete_trace_id" ] && echo "  Delete: $delete_trace_id"

echo ""
echo "Next Steps:"
echo "1. Check Loki in Grafana: http://$GRAFANA_HOST/explore"
echo "2. Use the Loki queries above to filter your application logs"
echo "3. Look for 'View Trace' buttons next to logs with trace IDs"
echo "4. If traces aren't found in Tempo, check the collector logs"

# Cleanup
rm -f *_headers.txt *_response.json

echo ""
echo "Test completed!"