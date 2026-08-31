"""Browser-side behavior checks for the query-string prefill script."""
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent

NODE_TEST = r"""
const fs = require("fs");
const vm = require("vm");
const source = fs.readFileSync("static/query-prefill.js", "utf8");

function run(search, includeUsername = true) {
    const fields = {
        "project-id": { value: "manual-project", events: [] },
    };
    if (includeUsername) {
        fields.username = { value: "manual-user", events: [] };
    }
    for (const field of Object.values(fields)) {
        field.dispatchEvent = event => field.events.push(event.type);
    }
    const context = {
        URLSearchParams,
        Event: class { constructor(type) { this.type = type; } },
        document: { getElementById: id => fields[id] || null },
        window: { location: { search } },
    };
    vm.runInNewContext(source, context);
    return fields;
}

const populated = run(
    "?project_id=EA%2074%2B&username=KLONDIKE%5Cmatt+wilkie"
);
if (populated["project-id"].value !== "EA 74+") {
    throw new Error("project ID was not decoded");
}
if (populated.username.value !== "KLONDIKE\\matt wilkie") {
    throw new Error("username was not decoded");
}
if (populated["project-id"].events.join() !== "input") {
    throw new Error("project ID input event was not dispatched");
}
if (populated.username.events.join() !== "input") {
    throw new Error("username input event was not dispatched");
}

const absent = run("");
if (absent["project-id"].value !== "manual-project") {
    throw new Error("an absent project_id changed the field");
}
if (absent.username.value !== "manual-user") {
    throw new Error("an absent username changed the field");
}

const empty = run("?project_id=&username=");
if (empty["project-id"].value !== "" || empty.username.value !== "") {
    throw new Error("explicit empty values did not clear the fields");
}

run("?project_id=EA-74", false);
"""


def test_query_prefill_browser_behavior():
    node = shutil.which("node")
    assert node is not None, "Node.js is required for JavaScript behavior tests"

    result = subprocess.run(
        [node, "-"],
        cwd=ROOT,
        input=NODE_TEST,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
