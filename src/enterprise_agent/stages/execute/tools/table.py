def compose_table(self, title, summary, rows, source_ids, columns=None):
    return self.save(
        "table",
        title,
        summary,
        rows,
        source_ids,
        {"columns": columns or [], "provenance": {"method": "table_compose"}},
    )
