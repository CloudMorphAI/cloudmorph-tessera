"""Collect a fresh, bounded, read-only workload preflight.

This checks fixed AWS identity, two pinned Amplify jobs, Nova Micro model
metadata, and the complete matching on-demand Price List pages. It never invokes
the model, performs OAuth, writes to AWS, or prints provider exception text.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

ACCOUNT_ID = "237509402889"
REGION = "us-east-1"
MODEL_ID = "amazon.nova-micro-v1:0"
MAX_SECONDS = 75
MAX_PRICE_PAGES = 3
MAX_PRICE_ROWS = 300
MAX_OUTPUT_BYTES = 65536
WATCHDOG_TERMINATE_SECONDS = 2
WATCHDOG_KILL_SECONDS = 2
TARGETS = (
    ("Tessera UI deployment", "d2nxbpvv53qeqc", "84"),
    ("Labs deployment", "d3s9n6ffim4722", "60"),
)
SDK_CONFIG = Config(
    connect_timeout=5,
    read_timeout=15,
    retries={"max_attempts": 0, "mode": "standard"},
)


class PreflightFailureError(Exception):
    """Finite local error code; underlying provider text is intentionally hidden."""


def _client(service: str, region: str = REGION) -> Any:
    return boto3.client(service, region_name=region, config=SDK_CONFIG)


def _read_identity() -> dict[str, Any]:
    identity = _client("sts").get_caller_identity()
    account = identity.get("Account")
    if account != ACCOUNT_ID:
        raise PreflightFailureError("ACCOUNT_MISMATCH")
    return {"account": account, "operation": "sts.GetCallerIdentity"}


def _read_job(label: str, app_id: str, job_id: str) -> dict[str, Any]:
    response = _client("amplify").get_job(appId=app_id, branchName="main", jobId=job_id)
    job = response.get("job")
    if type(job) is not dict:
        raise PreflightFailureError("JOB_RESPONSE_INVALID")
    summary = job.get("summary")
    if type(summary) is not dict:
        raise PreflightFailureError("JOB_SUMMARY_MISSING")
    return {
        "label": label,
        "operation": "amplify.GetJob",
        "app_id": app_id,
        "branch": "main",
        "job_id": job_id,
        "status": summary.get("status"),
        "commit_id": summary.get("commitId"),
    }


def _read_model() -> dict[str, Any]:
    response = _client("bedrock").get_foundation_model(modelIdentifier=MODEL_ID)
    details = response.get("modelDetails")
    if type(details) is not dict or details.get("modelId") != MODEL_ID:
        raise PreflightFailureError("MODEL_METADATA_INVALID")
    lifecycle = details.get("modelLifecycle")
    if type(lifecycle) is not dict or not isinstance(lifecycle.get("status"), str):
        raise PreflightFailureError("MODEL_LIFECYCLE_INVALID")
    model = {
        key: details.get(key)
        for key in (
            "modelId", "modelName", "providerName", "inputModalities",
            "outputModalities", "inferenceTypesSupported",
        )
    }
    model["modelLifecycle"] = {"status": lifecycle["status"]}
    return model


def _price_page(token: str | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {
        "ServiceCode": "AmazonBedrock",
        "Filters": [
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": REGION},
            {"Type": "TERM_MATCH", "Field": "model", "Value": "Nova Micro"},
        ],
        "MaxResults": 100,
    }
    if token:
        args["NextToken"] = token
    return _client("pricing", "us-east-1").get_products(**args)


def _extract_on_demand_prices(pages: list[dict[str, Any]]) -> dict[str, Any]:
    found: dict[str, set[tuple[str, str, str]]] = {"Input tokens": set(), "Output tokens": set()}
    row_count = 0
    for page in pages:
        price_rows = page.get("PriceList")
        if type(price_rows) is not list:
            raise PreflightFailureError("PRICE_RESPONSE_INVALID")
        row_count += len(price_rows)
        if row_count > MAX_PRICE_ROWS:
            raise PreflightFailureError("PRICE_ROW_LIMIT")
        for raw in price_rows:
            try:
                product = json.loads(raw)
                attrs = product["product"]["attributes"]
                if (
                    attrs.get("regionCode") != REGION
                    or attrs.get("model") != "Nova Micro"
                    or attrs.get("feature") != "On-demand Inference"
                    or attrs.get("inferenceType") not in found
                ):
                    continue
                dimensions: list[tuple[str, str]] = []
                for term in product["terms"]["OnDemand"].values():
                    for dimension in term.get("priceDimensions", {}).values():
                        unit = dimension.get("unit")
                        usd = dimension.get("pricePerUnit", {}).get("USD")
                        if unit == "1K tokens" and isinstance(usd, str):
                            dimensions.append((unit, usd))
                if len(set(dimensions)) != 1:
                    continue
                unit, usd = dimensions[0]
                inference_type = attrs["inferenceType"]
                sku = product["product"].get("sku")
                if not isinstance(sku, str) or not sku:
                    raise PreflightFailureError("PRICE_SKU_MISSING")
                found[inference_type].add((sku, unit, usd))
            except (KeyError, TypeError, ValueError):
                raise PreflightFailureError("PRICE_PRODUCT_INVALID") from None
    rates: dict[str, str] = {}
    selected: list[dict[str, str]] = []
    for inference_type, key in (("Input tokens", "input_usd_per_1000"), ("Output tokens", "output_usd_per_1000")):
        choices = found[inference_type]
        if len(choices) != 1:
            raise PreflightFailureError("PRICE_MATCH_NOT_UNIQUE")
        sku, unit, price = next(iter(choices))
        if not price.isdecimal() and not _valid_decimal(price):
            raise PreflightFailureError("PRICE_VALUE_INVALID")
        rates[key] = price
        selected.append({"sku": sku, "unit": unit, "usd_per_unit": price, "inference_type": inference_type})
    # Existing approved nomenclature: six requests at 12,000 bytes (2 tokens
    # per byte) plus six responses capped at 1,024 tokens.
    input_tokens = 6 * 12000 * 2
    output_tokens = 6 * 1024
    cost = (
        Decimal(input_tokens) * Decimal(rates["input_usd_per_1000"]) / 1000
        + Decimal(output_tokens) * Decimal(rates["output_usd_per_1000"]) / 1000
    )
    return {
        "rates": rates,
        "selected_products": selected,
        "price_pagination_complete": True,
        "price_pages": len(pages),
        "price_products_scanned": row_count,
        "cost_bound_usd": format(cost.quantize(Decimal("0.0000000001")), "f"),
    }


def _valid_decimal(value: str) -> bool:
    try:
        return Decimal(value).is_finite() and Decimal(value) >= 0
    except Exception:
        return False


def collect() -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + MAX_SECONDS
    jobs: list[tuple[str, str, str]] = list(TARGETS)
    pages: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_read_identity): "identity",
            pool.submit(_read_job, *jobs[0]): "ui_job",
            pool.submit(_read_job, *jobs[1]): "labs_job",
            pool.submit(_read_model): "model",
            pool.submit(_price_page): "price_page_1",
        }
        results: dict[str, Any] = {}
        try:
            for future in as_completed(futures, timeout=max(0.1, deadline - time.monotonic())):
                results[futures[future]] = future.result()
        except Exception:
            for future in futures:
                future.cancel()
            raise PreflightFailureError("INITIAL_READ_FAILED_OR_TIMEOUT") from None
    if len(results) != len(futures):
        raise PreflightFailureError("INITIAL_READ_INCOMPLETE")
    pages.append(results.pop("price_page_1"))
    while pages[-1].get("NextToken"):
        if len(pages) >= MAX_PRICE_PAGES or time.monotonic() >= deadline:
            raise PreflightFailureError("PRICE_PAGINATION_INCOMPLETE")
        try:
            pages.append(_price_page(pages[-1]["NextToken"]))
        except Exception:
            raise PreflightFailureError("PRICE_READ_FAILED") from None
    prices = _extract_on_demand_prices(pages)
    model = results["model"]
    if model.get("modelLifecycle", {}).get("status") != "ACTIVE":
        raise PreflightFailureError("MODEL_NOT_ACTIVE")
    if "TEXT" not in model.get("inputModalities", []) or "TEXT" not in model.get("outputModalities", []):
        raise PreflightFailureError("MODEL_MODALITY_MISMATCH")
    if "ON_DEMAND" not in model.get("inferenceTypesSupported", []):
        raise PreflightFailureError("MODEL_ON_DEMAND_UNAVAILABLE")
    for key in ("ui_job", "labs_job"):
        if results[key].get("status") != "SUCCEED" or not results[key].get("commit_id"):
            raise PreflightFailureError("JOB_NOT_SUCCEEDED")
    if time.monotonic() > deadline:
        raise PreflightFailureError("OVERALL_TIMEOUT")
    return {
        "schema": "tessera.workload_fresh_readonly_preflight.v1",
        "status": "PASS",
        "at": datetime.now(UTC).isoformat(),
        "account": results["identity"]["account"],
        "region": REGION,
        "targets": [results["ui_job"], results["labs_job"]],
        "model": model,
        "rates": prices["rates"],
        "selected_price_products": prices["selected_products"],
        "price_pagination_complete": prices["price_pagination_complete"],
        "price_pages": prices["price_pages"],
        "price_products_scanned": prices["price_products_scanned"],
        "cost_bound_usd": prices["cost_bound_usd"],
        "inference_calls": 0,
        "oauth_calls": 0,
        "cloud_writes": 0,
        "scope": "Direct SDK preflight reads only; not agent/Tessera/MCP enforcement acceptance",
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new receipt path; file must not already exist")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            result = collect()
            encoded = json.dumps({"status": "PASS", "preflight": result}, sort_keys=True, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise PreflightFailureError("OUTPUT_LIMIT")
            print(encoded)
            return 0
        except BaseException as exc:
            code = exc.args[0] if isinstance(exc, PreflightFailureError) and exc.args else "PREFLIGHT_FAILED"
            print(json.dumps({"status": "FAIL", "failure": str(code)}, sort_keys=True, separators=(",", ":")))
            return 1
    if args.output is None:
        parser.error("--output is required")
    started = time.monotonic()
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker"]
    child: subprocess.Popen[bytes] | None = None
    failure: str | None = None
    result: dict[str, Any] | None = None
    try:
        child = subprocess.Popen(  # noqa: S603 -- fixed script path/worker flag; no shell or user-controlled command
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
        )
        try:
            stdout, _ = child.communicate(timeout=MAX_SECONDS)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                stdout, _ = child.communicate(timeout=WATCHDOG_TERMINATE_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, _ = child.communicate(timeout=WATCHDOG_KILL_SECONDS)
            failure = "OVERALL_TIMEOUT"
        if failure is None:
            if len(stdout) > MAX_OUTPUT_BYTES:
                failure = "OUTPUT_LIMIT"
            else:
                try:
                    child_result = json.loads(stdout)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    failure = "WORKER_RESULT_INVALID"
                else:
                    if child_result.get("status") == "PASS" and child.returncode == 0 and type(child_result.get("preflight")) is dict:
                        result = child_result["preflight"]
                    else:
                        worker_failure = child_result.get("failure")
                        failure = worker_failure if isinstance(worker_failure, str) and worker_failure.isascii() else "WORKER_FAILED"
    except Exception:
        failure = "WORKER_START_FAILED"
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            try:
                child.wait(timeout=WATCHDOG_KILL_SECONDS)
            except subprocess.TimeoutExpired:
                failure = "WORKER_SHUTDOWN_UNCONFIRMED"
    if failure is None and result is not None:
        receipt = result
    else:
        receipt = {
            "schema": "tessera.workload_fresh_readonly_preflight.v1",
            "status": "FAIL",
            "at": datetime.now(UTC).isoformat(),
            "failure": failure or "PREFLIGHT_FAILED",
            "inference_calls": 0,
            "oauth_calls": 0,
            "cloud_writes": 0,
            "scope": "Direct SDK preflight reads only; no inference, OAuth or cloud writes",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    encoded = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_OUTPUT_BYTES:
        return 1
    try:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
    except Exception:
        print(json.dumps({"status": "FAIL", "failure": "RECEIPT_CREATE_FAILED"}))
        return 1
    print(json.dumps({"status": receipt["status"], "receipt": str(args.output), "bytes": len(encoded)}))
    return 0 if receipt["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
