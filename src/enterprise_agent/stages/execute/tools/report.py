def render_report(self, title, body, source_ids):
    return self.save(
        "report",
        title,
        title,
        body,
        source_ids,
        {"title": title, "format": "markdown"},
    )
