# Kubernetes prototype

These manifests run the API and Micro-XRCE-DDS agent in a local Kubernetes cluster. Build the API image from the project root and load it into the cluster first:

```bash
docker build -f backend/Dockerfile -t aerodrop-backend:local .
kubectl apply -f infra/k8s/namespace.yaml
```

The API is exposed on node port 30800. Access it with `kubectl port-forward -n aerodrop service/aerodrop-backend 8000:8000` and open `http://127.0.0.1:8000`.

The XRCE agent uses host networking for PX4 UDP discovery. PX4 SITL and the ROS 2 mission executor are intentionally kept in Docker Compose: the simulator requires Gazebo-specific runtime support, and the ROS node must share DDS networking with PX4. The AKS context commonly present in developer kubeconfigs should not be used for this local demo; select a disposable local context before applying.

```bash
kubectl config use-context <your-local-cluster>
kubectl apply -f infra/k8s/namespace.yaml
```
