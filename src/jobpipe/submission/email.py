SUBJECTS = {"fr": "Candidature — {title}", "en": "Application — {title}"}
BODIES = {
    "fr": (
        "Madame, Monsieur,\n\nVeuillez trouver ci-joint ma candidature pour le poste de {title}, avec mon CV et ma lettre de "
        "motivation. Je reste à votre disposition pour tout échange.\n\nCordialement,\n{name}"
    ),
    "en": (
        "Dear Sir or Madam,\n\nPlease find attached my application for the {title} position, with my CV and cover letter. "
        "I remain at your disposal for any discussion.\n\nKind regards,\n{name}"
    ),
}


def build_subject(template: str | None, title: str, language: str) -> str:
    if template:
        return template
    return SUBJECTS.get(language, SUBJECTS["en"]).format(title=title)


def build_body(language: str, title: str, name: str) -> str:
    return BODIES.get(language, BODIES["en"]).format(title=title, name=name)


def reply_address(sender: str, token: str) -> str:
    local, _, domain = sender.partition("@")
    return f"{local.split('+', 1)[0]}+{token}@{domain}"
