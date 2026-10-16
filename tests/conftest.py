import pytest

from manga.models import Project
from manga.prompts import story_template


@pytest.fixture
def manga_project():
    return Project(title="月の図書館", genre="ファンタジー", synopsis="司書が失われた物語を探す。")


@pytest.fixture
def story(manga_project):
    data = story_template(manga_project)
    data["summary"] = "月の司書が、忘れられた物語を読者に届ける。"
    for page in data["pages"]:
        page["summary"] = f"物語の{page['page']}番目の場面。"
        for panel in page["panels"]:
            panel["description"] = f"{page['page']}ページ{panel['panel']}コマ。司書が本を開く。"
            panel["dialogue"] = "司書：この本を届けよう。"
    return data
