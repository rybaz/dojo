import sqlalchemy
import subprocess
import tempfile
import logging
import pathlib
import shutil
import docker
import pathlib
import os
import re

from flask import request
from flask_restx import Namespace, Resource
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import and_
from CTFd.models import db, Solves, Challenges
from CTFd.utils.decorators import authed_only, admins_only
from CTFd.utils.user import get_current_user, is_admin
from CTFd.utils.modes import get_model
from CTFd.utils.security.sanitize import sanitize_html

from ...models import Dojos, OfficialDojos, DojoMembers, DojoAdmins
from ...utils.dojo import dojo_accessible, dojo_clone, dojo_update, load_dojo_dir


dojo_namespace = Namespace(
    "dojo", description="Endpoint to manage dojos"
)


@dojo_namespace.route("/create")
class CreateDojo(Resource):
    @authed_only
    @admins_only  # TODO: allow anyone to create a dojo
    def post(self):
        data = request.get_json()
        user = get_current_user()

        DOJO_EXISTS = "This repository already exists as a dojo"

        try:
            repository = data.get("repository", "")
            public_key = data.get("public_key", "")
            private_key = data.get("private_key", "").replace("\r\n", "\n")

            repository_re = r"[\w\-]+/[\w\-]+"
            assert re.match(repository_re, repository), f"Invalid repository, expected format: <code>{repository_re}</code>"

            assert not Dojos.query.filter_by(repository=repository).first(), DOJO_EXISTS

            dojo_dir = dojo_clone(repository, private_key)
            dojo_path = pathlib.Path(dojo_dir.name)

            dojo = load_dojo_dir(dojo_path, dojo_type="official")  # TODO DEBUG: dojo_type should not be set
            dojo.repository = repository
            dojo.public_key = public_key
            dojo.private_key = private_key
            dojo.admins = [DojoAdmins(user=user)]

            db.session.add(dojo)
            db.session.commit()

            dojo.directory.parent.mkdir(exist_ok=True)
            dojo_path.rename(dojo.directory)
            dojo_path.mkdir()  # TODO: ignore_cleanup_errors=True

        except subprocess.CalledProcessError as e:
            print(e, e.stderr, flush=True)
            deploy_url = f"https://github.com/{repository}/settings/keys"
            return {"success": False, "error": f'Failed to clone: <a href="{deploy_url}" target="_blank">add deploy key</a>'}, 400

        except IntegrityError:
            return {"success": False, "error": DOJO_EXISTS}, 400

        except AssertionError as e:
            return {"success": False, "error": str(e)}, 400

        return {"success": True, "dojo_id": dojo.dojo_id}


@dojo_namespace.route("/update")
class UpdateDojo(Resource):
    def post(self):
        data = request.get_json()

        try:
            update_code = data.get("update_code", "")
            private_key = Dojos.private_key_from_update_code(update_code)

            dojo = Dojos.query.filter_by(private_key=private_key).first()
            assert dojo, "Dojo not found"

            dojo_update(dojo)

        except AssertionError as e:
            return {"success": False, "error": str(e)}, 400

        return {"success": True, "hash": dojo.hash}


# TODO: /leave

@dojo_namespace.route("/solves")
class DojoSolves(Resource):
    @authed_only
    def get(self):

        Model = get_model()
        fields = {
            "account_id": Solves.account_id,
            "account_name": Model.name,
            "account_email": Model.email,
            "challenge_id": Challenges.id,
            "challenge_category": Challenges.category,
            "challenge_name": Challenges.name,
            "solve_time": Solves.date,
        }
        standings = (
            dojo_standings(dojo_id, fields.values())
            .order_by(Solves.id)
            .all()
        )
        return [dict(zip(fields, standing)) for standing in standings]
