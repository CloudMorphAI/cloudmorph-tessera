.PHONY: sbom docker-build-repro

sbom:
	cyclonedx-py environment -o sbom.json
	@echo "SBOM written to sbom.json"

docker-build-repro:
	docker build \
		--build-arg SOURCE_DATE_EPOCH=$$(git log -1 --format=%ct) \
		-t tessera-repro:dev \
		.
	@echo "Built tessera-repro:dev"
