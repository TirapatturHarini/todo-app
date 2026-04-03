#!/usr/bin/env bash

set -u

if [ $# -lt 2 ]; then
    echo "Usage: $0 <kubeconfig_path> <cluster_name> [namespace|all]"
    exit 1
fi

KUBECONFIG_PATH="$1"
CLUSTER_NAME="$2"
if [ ! -f "$KUBECONFIG_PATH" ]; then
    echo "Error: kubeconfig file not found at: $KUBECONFIG_PATH"
    exit 1
fi

export KUBECONFIG="$KUBECONFIG_PATH"

if [ $# -ge 3 ]; then
    TARGET_NS="$3"
else
    TARGET_NS="$(kubectl config view --minify --output 'jsonpath={..namespace}' 2>/dev/null)"
    if [ -z "$TARGET_NS" ]; then
        TARGET_NS="default"
    fi
fi

LOKI_URL="${LOKI_URL:-http://localhost:3100/loki/api/v1/push}"
PID_PREFIX="/tmp/loki_stream_${CLUSTER_NAME}_"

echo "Using kubeconfig: $KUBECONFIG_PATH"
echo "Cluster label: $CLUSTER_NAME"
echo "Namespace scope: $TARGET_NS"
echo "Pushing logs to: $LOKI_URL"

cleanup() {
    for pidfile in "${PID_PREFIX}"*.pid; do
        [ -e "$pidfile" ] || continue
        pid="$(cat "$pidfile" 2>/dev/null || true)"
        if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
        rm -f "$pidfile"
    done
    exit 0
}
trap cleanup INT TERM

json_escape() {
    sed 's/\\/\\\\/g; s/"/\\"/g'
}

stream_container() {
    ns="$1"
    pod="$2"
    container="$3"

    kubectl logs -f "$pod" -c "$container" -n "$ns" --tail=20 2>/dev/null | \
    while IFS= read -r line; do
        ts="$(date +%s%N)"
        esc_line="$(printf '%s' "$line" | json_escape)"
        payload="{\"streams\":[{\"stream\":{\"cluster\":\"$CLUSTER_NAME\",\"namespace\":\"$ns\",\"pod\":\"$pod\",\"container\":\"$container\"},\"values\":[[\"$ts\",\"$esc_line\"]]}]}"
        curl -s -H "Content-Type: application/json" -X POST -d "$payload" "$LOKI_URL" >/dev/null 2>&1 || true
    done
}

list_running_pods() {
    if [ "$TARGET_NS" = "all" ]; then
        kubectl get pods -A --field-selector=status.phase=Running \
            -o custom-columns=NS:.metadata.namespace,POD:.metadata.name --no-headers 2>/dev/null || true
    else
        kubectl get pods -n "$TARGET_NS" --field-selector=status.phase=Running \
            -o custom-columns=NS:.metadata.namespace,POD:.metadata.name --no-headers 2>/dev/null || true
    fi
}

while true; do
    list_running_pods | while IFS=' ' read -r ns pod; do
        [ -n "${ns:-}" ] || continue
        [ -n "${pod:-}" ] || continue

        containers="$(kubectl get pod "$pod" -n "$ns" -o jsonpath='{.spec.containers[*].name}' 2>/dev/null || true)"
        for container in $containers; do
            safe_ns="$(printf '%s' "$ns" | tr '/:' '__')"
            safe_pod="$(printf '%s' "$pod" | tr '/:' '__')"
            safe_container="$(printf '%s' "$container" | tr '/:' '__')"
            pidfile="${PID_PREFIX}${safe_ns}_${safe_pod}_${safe_container}.pid"

            if [ -f "$pidfile" ]; then
                oldpid="$(cat "$pidfile" 2>/dev/null || true)"
                if [ -n "${oldpid:-}" ] && kill -0 "$oldpid" 2>/dev/null; then
                    continue
                fi
            fi

            stream_container "$ns" "$pod" "$container" &
            echo "$!" > "$pidfile"
            echo "Started stream: cluster=$CLUSTER_NAME namespace=$ns pod=$pod container=$container"
        done
    done

    sleep 10
done

