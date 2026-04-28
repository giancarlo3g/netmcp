#!/bin/bash

# Bash script to run containerlab nodes in MacOS
# unable to connect directly to the containers from MacOS yet

start() {
    docker run --rm -it \
        --privileged \
        --network host \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v /var/run/netns:/var/run/netns \
        -v /var/lib/docker/containers:/var/lib/docker/containers \
        -v $PWD:$PWD \
        -w $PWD \
        --pid="host" \
        ghcr.io/srl-labs/clab:latest \
        clab deploy
}

stop() {
    docker run --rm -it \
        --privileged \
        --network host \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v /var/run/netns:/var/run/netns \
        -v /var/lib/docker/containers:/var/lib/docker/containers \
        -v $PWD:$PWD \
        -w $PWD \
        ghcr.io/srl-labs/clab:latest \
        clab destroy
}

main () {
    local cmd="${1:-run}"
    case "$cmd" in
        run)  start ;;
        stop) stop ;;
        *)    echo "Usage: $0 [run|stop]" >&2; exit 1 ;;
    esac
}

main "$@"