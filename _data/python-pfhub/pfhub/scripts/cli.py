"""The CLI command line tool
"""

import re
import os
import tempfile
import shutil
import sys

import click
import click_params
from toolz.curried import pipe, get
from toolz.curried import map as map_
import pykwalify
import pykwalify.core
import requests
import linkml.validators.jsonschemavalidator as validator

from .. import test as pfhub_test
from ..convert import meta_to_zenodo_no_zip, download_file
from ..convert import download_zenodo as download_zenodo_
from ..convert import download_meta as download_meta_
from ..func import compact


EPILOG = "See the documentation at \
https://github.com/usnistgov/pfhub/blob/master/CLI.md (under construction)"


@click.group(epilog=EPILOG)
def cli():
    """Submit results to PFHub and manipulate PFHub data"""


@cli.command(epilog=EPILOG)
@click.argument("url", type=click_params.URL)
@click.option(
    "--dest",
    "-d",
    help="destination directory",
    default="./",
    type=click.Path(exists=True, writable=True, file_okay=False),
)
def download(url, dest):
    """Download a Zenodo record

    Works with any Zenodo link

    Args:
      url: the URL of either a meta.yaml or Zenodo record
      dest: the destination directory
    """
    record_id = get_zenodo_record_id(url, zenodo_regexs())
    sandbox = False
    if record_id is None:
        record_id = get_zenodo_record_id(url, zenodo_sandbox_regexs())
        sandbox = True

    if record_id is not None:
        local_filepaths = download_zenodo_(record_id, sandbox=sandbox, dest=dest)
        output(local_filepaths)
    else:
        click.secho(f"{url} does not match any expected regex for Zenodo", fg="red")
        sys.exit(1)


@cli.command(epilog=EPILOG)
@click.argument("url", type=click_params.URL)
@click.option(
    "--dest",
    "-d",
    help="destination directory",
    default="./",
    type=click.Path(exists=True, file_okay=False, writable=True),
)
def download_meta(url, dest):
    """Download an old style PFHub YAML file

    In addition, gets the linked data in the `data` section

    Args:
      url: the URL of either the YAML file
      dest: the destination directory
    """
    try:
        is_meta = validate_old_url(url)
    except requests.exceptions.ConnectionError as err:
        click.secho(err, fg="red")
        click.secho(f"{url} is invalid", fg="red")
        sys.exit(1)
    except IsADirectoryError:
        click.secho(f"{url} is not a link to a file", fg="red")
        sys.exit(1)

    if is_meta:
        local_filepaths = download_meta_(url, dest=dest)
        output(local_filepaths)
    else:
        click.secho(f"{url} is not valid", fg="red")
        sys.exit(1)


@cli.command(epilog=EPILOG)
@click.argument(
    "file_path", type=click.Path(exists=True, dir_okay=False, readable=True)
)
@click.option(
    "--dest",
    "-d",
    help="destination directory",
    default="./",
    type=click.Path(exists=False, writable=True, file_okay=False),
)
def convert(file_path, dest):
    """Convert an old style PFHub YAML file to the new schema

    Args:
      file_path: the file path to the old style PFHub YAML
      dest: the destination directory

    """
    is_meta = validate_old_(file_path)
    if is_meta:
        local_filepaths = meta_to_zenodo_no_zip(file_path, dest)
        output(local_filepaths)
    else:
        click.secho(f"{file_path} is not valid", fg="red")
        sys.exit(1)


@cli.command(epilog=EPILOG)
@click.argument(
    "file_path", type=click.Path(exists=True, dir_okay=False, readable=True)
)
def validate_old(file_path):
    """Validate a PFHub YAML file against the old schema

    Args:
      file_path: the URL of the meta.yaml

    """
    if validate_old_(file_path):
        click.secho(f"{file_path} is valid", fg="green")
    else:
        click.secho(f"{file_path} is not valid", fg="red")
        sys.exit(1)


@cli.command(epilog=EPILOG)
@click.argument(
    "file_path", type=click.Path(exists=True, dir_okay=False, readable=True)
)
def validate(file_path):
    """Validate a PFHub YAML file against the new schema

    Args:
      file_path: the URL of the meta.yaml

    """
    if validate_(file_path):
        click.secho(f"{file_path} is valid", fg="green")
    else:
        click.secho(f"{file_path} is not valid", fg="red")
        sys.exit(1)


@cli.command(epilog=EPILOG)
def test():  # pragma: no cover
    """Run the PFHub tests

    Currently creates a stray .coverage file when running.
    """
    pfhub_test()


def zenodo_regexs():
    """Regular expression for acceptable Zenodo URLs"""
    return [
        r"https://doi.org/10.5281/zenodo.(\d{1,})",
        r"https://zenodo.org/api/records/(\d{1,})",
        r"https://zenodo.org/record/(\d{1,})",
    ]


def zenodo_sandbox_regexs():
    """Regular expression for acceptable Zenodo sandbox URLs"""
    return [
        r"https://sandbox.zenodo.org/record/(\d{1,})",
        r"https://sandbox.zenodo.org/api/records/(\d{1,})",
    ]


def get_zenodo_record_id(url, regexs):
    """Get the record from a Zenodo URL

    Args:
      url: any URL (doesn't need to be Zenodo)
      regexs: acceptable regexs

    Returns:
      Record ID or None
    """
    return pipe(
        regexs,
        map_(lambda x: re.fullmatch(x, url)),
        compact,
        map_(lambda x: x.groups()[0]),
        list,
        get(0, default=None),
    )


def validate_old_url(url):
    """Validate that a URL link against the old schema

    Args:
      url: the url for the file
    """
    tmpdir = tempfile.mkdtemp()
    file_path = download_file(url, dest=tmpdir)
    result = validate_old_(file_path)
    shutil.rmtree(tmpdir)
    return result


def validate_old_(path):
    """Validate a file against the old schema

    Args:
      path: the path to the file
    """
    schema_file = os.path.join(
        os.path.split(__file__)[0], "..", "schema", "schema_meta.yaml"
    )
    try:
        obj = pykwalify.core.Core(source_file=path, schema_files=[schema_file])
    except pykwalify.errors.CoreError:
        return False
    try:
        obj.validate(raise_exception=True)
    except pykwalify.errors.SchemaError:
        return False
    return True


def validate_(path):
    """Validate a YAML file against the new schema

    Uses linkml

    Args:
      path: path to the YAML file

    Returns:
      whether the schema is valid
    """
    schema_file = os.path.join(
        os.path.split(__file__)[0], "..", "schema", "pfhub_schema.yaml"
    )
    try:
        validator.cli.callback(path, None, None, schema=schema_file)
    except SystemExit as error:
        return error.code == 0
    except KeyError as error:
        print(f"KeyError: {error}")
        return False
    raise RuntimeError(
        "the linkml validator did not exit correctly"
    )  # pragma: no cover


def output(local_filepaths):
    """Output formatted file names with commas to stdout

    Args:
      local_filepaths: list of file path strings
    """

    def echo(local_filepath, newline, comma=","):
        formatted_path = click.format_filename(local_filepath)
        click.secho(message=f" {formatted_path}" + comma, fg="green", nl=newline)

    click.secho(message="Writing:", fg="green", nl=False)
    for local_filepath in local_filepaths[:-1]:
        echo(local_filepath, False)
    echo(local_filepaths[-1], True, comma="")