# k8s/

Reference Kubernetes manifests for each service in this monorepo. **Templates,
not the source of truth** — actual deployments live in environment-specific
repos (e.g. `non-prod-kubernetes/{development,internal-test,customer-test}/apps/`,
`aks-prod-kubernetes/customer-prod/apps/`).

## Layout

```
k8s/
└── mcp-template/
    ├── mcp-template-depl.yaml
    └── mcp-template-serv.yaml
```

Directories use **dashes** (matching the K8s `metadata.name`), unlike `svc/`
which uses underscores (Python module convention).

## Defaults baked in

- `namespace: development` — change per environment
- `replicas: 1` — required for services using `lib.mcp_service.state.TTLStore`
  (process-local). Don't bump without switching to a shared store.
- `image: borndigitalaibot/<name>:latest` — pin to a real tag before applying
- `APP_LOGSTASH_HOST: '10.4.0.6'`, `APP_LOGSTASH_PORT: '5959'` — dev cluster
  Logstash; override per environment
- `APP_MCP_AUTH_ENABLED: 'False'` — flip to `'True'` and wire up the
  `APP_MCP_AUTH_API_KEY` secret reference for any non-public deployment
- Healthz probes hit `/healthz/liveness` and `/healthz/readiness` on port 8080
- Prometheus scrape annotations target `:8080/healthz/metrics`

## Deploying

```bash
# Apply directly (one-off, dev cluster)
kubectl apply -f k8s/mcp-template/

# Or copy into the environment repo and let the GitOps pipeline pick them up
cp -r k8s/mcp-template ../non-prod-kubernetes/development/apps/
```

## Editing checklist before applying

- [ ] `image:` tag set to a real Docker Hub tag (not `:latest`)
- [ ] `namespace:` matches the target environment
- [ ] Logstash host/port matches the cluster (10.4.0.6 only valid in non-prod)
- [ ] `APP_GIT_COMMIT` overridden via build-arg in the image (defaults to "local")
- [ ] If auth is needed: create `mcp-server-secret` with `mcp-auth-api-key` in
      that namespace before flipping `APP_MCP_AUTH_ENABLED` to True
