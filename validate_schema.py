"""Check manifests against Kubernetes upstream OpenAPI v3 schemas."""
import argparse
import json
from pathlib import Path
from urllib.request import urlopen

from jsonschema import Draft7Validator

BASE = ("https://raw.githubusercontent.com/kubernetes/kubernetes/"
        "v1.36.0/api/openapi-spec/v3/")
ROOT = Path(__file__).resolve().parent
TARGETS = {
    "deployment.json": ("apis__apps__v1", "io.k8s.api.apps.v1.Deployment"),
    "service.json": ("api__v1", "io.k8s.api.core.v1.Service"),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, help="Use local schemas instead of network")
    args = parser.parse_args()
    for manifest, (group, name) in TARGETS.items():
        filename = f"{group}_openapi.json"
        if args.cache:
            document = json.loads((args.cache / filename).read_text())
        else:
            with urlopen(BASE + filename, timeout=25) as response:
                document = json.load(response)
        # Keep the component definitions at the document root for local refs.
        validator = Draft7Validator({
            "$ref": f"#/components/schemas/{name}",
            "components": document["components"],
        })
        validator.validate(json.loads((ROOT / manifest).read_text()))
        print(f"{manifest}: upstream v1.36.0 OpenAPI schema passed")


if __name__ == "__main__":
    main()
