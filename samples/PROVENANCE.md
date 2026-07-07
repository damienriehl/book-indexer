# Sample document provenance

Every file under `samples/` is either synthetic (authored for this project) or a
U.S. federal government edict in the public domain. Neither is a third-party
copyrighted work, so both may be redistributed under book-indexer's AGPL-3.0
license without additional permission.

## `synthetic_treatise.pdf`

- **Source:** authored for book-indexer. Regenerate deterministically with
  `uv run python samples/build_synthetic_treatise.py`.
- **Content:** a 10-page synthetic legal treatise (invented doctrine, invented
  citations, invented section hierarchy) designed to exercise every stage of the
  pipeline: printed folios, section headings (`§`/Chapter), footnotes,
  hyphenation across line breaks, and citation forms.
- **License:** original synthetic content, released under AGPL-3.0 with the
  package. No third-party text.

## `scotus_slip_opinion.pdf`

- **Source:** Supreme Court of the United States, *Official Reports* preliminary
  print, Volume 606 U.S. Part 2 (pages 748-830), downloaded from
  <https://www.supremecourt.gov/opinions/24pdf/606us2r65_3314.pdf>
  (October Term 2024, decided June 27, 2025).
- **Content:** a real merits opinion with authentic printed page numbers
  (folios 748-830), section structure, and legal citations — used to demonstrate
  the indexer on genuine legal text.
- **License:** **public domain.** Works of the U.S. federal government, and
  judicial opinions specifically, are not copyrightable under the
  *government edicts doctrine* (*Georgia v. Public.Resource.Org, Inc.*,
  590 U.S. 296 (2020)) and 17 U.S.C. § 105.
- **Note:** the download is reproducible with the URL above; the file is
  committed so a fresh clone needs no network access to run the sample tests.
