"""The Vitality observer: turns each captured interaction into a Markdown observation.

It watches the server's captures, hands batches of unobserved interactions to Claude Code, and
keeps the resulting observations in the user model.
"""
