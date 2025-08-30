from bill_parser_engine.core.reference_resolver.target_identifier import TargetArticleIdentifier
from bill_parser_engine.core.reference_resolver.models import BillChunk


def main() -> None:
    chunk = BillChunk(
        text=(
            "a) Au 3° du II, les mots : « prévu aux articles L. 254-6-2 et L. 254-6-3 » "
            "sont remplacés par les mots : « à l'utilisation des produits phytopharmaceutiques » ;"
        ),
        titre_text="# TITRE Iᴱᴿ",
        article_label="Article 1",
        article_introductory_phrase="",
        major_subdivision_label="",
        major_subdivision_introductory_phrase="e code rural et de la pêche maritime est ainsi modifié :",
        numbered_point_label="2°",
        numbered_point_introductory_phrase="L'article L. 254-1 est ainsi modifié :",
        lettered_subdivision_label="a)",
        hierarchy_path=["# TITRE Iᴱᴿ", "Article 1", "", "2°", "a)"],
        chunk_id="# TITRE Iᴱᴿ::Article 1::2°::a)",
        start_pos=0,
        end_pos=0,
        target_article=None,
        inherited_target_article=None,
        structural_anchor_hint=None,
    )

    identifier = TargetArticleIdentifier(use_cache=False)
    res = identifier.identify(chunk)
    print("IDENTIFY:", res.operation_type.value, res.code, res.article, res.confidence)


if __name__ == "__main__":
    main()


