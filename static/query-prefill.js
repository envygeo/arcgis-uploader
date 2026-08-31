/* Prefill host-supplied values without interpreting them as HTML. */
(function () {
    "use strict";

    const queryFields = {
        project_id: "project-id",
        username: "username",
    };

    function prefillUploaderFields(search, root) {
        const params = new URLSearchParams(search);

        for (const [parameter, fieldId] of Object.entries(queryFields)) {
            if (!params.has(parameter)) continue;

            const field = root.getElementById(fieldId);
            if (!field) continue;

            field.value = params.get(parameter);
            field.dispatchEvent(new Event("input", { bubbles: true }));
        }
    }

    window.prefillUploaderFields = prefillUploaderFields;
    prefillUploaderFields(window.location.search, document);
})();
