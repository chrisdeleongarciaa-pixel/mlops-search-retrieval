"""Deploy the versioned witness application to a local Vespa config server."""

import argparse
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import ZIP_DEFLATED, ZipFile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config_url")
    args = parser.parse_args()
    root = Path(__file__).with_name("app")
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as bundle:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(root).as_posix())
    endpoint = args.config_url.rstrip("/") + "/application/v2/tenant/default/prepareandactivate"
    request = Request(endpoint, archive.getvalue(), {"Content-Type": "application/zip"}, method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            print(response.read().decode("utf-8"))
    except HTTPError as error:
        raise SystemExit(error.read().decode("utf-8")) from error


if __name__ == "__main__":
    main()
