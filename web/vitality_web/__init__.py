"""The Vitality web app: a private, read-only view of the user model (docs/superpowers/specs/2026-11-06-web-design.md).

Modules:
    schema.py     the API's response models (the contract with the app in `web/app`)
    model.py      `ModelReader`: the model and one interaction's evidence, read from the files
    modeler.py    `read_modeler`: the modeler's state, sessions and the operations waiting
    captures.py   listing and safely resolving screenshots
    app.py        `create_app`: the FastAPI application (API, screenshots, the built app)
    main.py       the `vitality-web` command
"""
