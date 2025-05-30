## 🎯 Objective

To process a paragraph from a legislative proposal (e.g. a French bill), detect all normative references it contains (to other articles, codes, regulations), and resolve them recursively in order to obtain a fully interpretable, self-contained version of the paragraph without unresolved references.

---

## 🧩 Problem Formulation

### Input

The content of an "Article" from a french legislative bill (reminder the hierarchie of content is something like Titre -> Article 1, Article 2 etc.), such as:

```
# Article 1ᵉʳ

Le code rural et de la pêche maritime est ainsi modifié :

1. (Supprimé)
2. L’article L. 254‑1 est ainsi modifié :
3. - a) (nouveau) Au 3° du II, les mots : « prévu aux articles L. 254‑6‑2 et 254‑6‑3 » sont remplacés par les mots : « à l’utilisation des produits phytopharmaceutiques » ;
- b) Le VI est ainsi modifié :

- – à la fin de la première phrase, les mots : « incompatible avec celui des activités mentionnées aux 1° ou 2° du II ou au IV » sont remplacés par les mots : « interdit aux producteurs au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 du 21 octobre 2009, sauf lorsque la production concerne des produits de biocontrôle figurant sur la liste mentionnée à l’article L. 253‑5 du présent code, des produits composés uniquement de substances de base au sens de l’article 23 du règlement (CE) n° 1107/2009 ou de produits à faible risque au sens de l’article 47 du même règlement (CE) n° 1107/2009 et des produits dont l’usage est autorisé dans le cadre de l’agriculture biologique » ;
- – la seconde phrase est supprimée ;

(Supprimé)
4. 3° bis (nouveau) L’article L. 254‑1‑1 est ainsi modifié :
---
# 3

– au 2°, les mots : « mentionnée aux 1° ou 2° du II ou au IV de l’article L. 254‑1 » sont remplacés par les mots : « de producteur au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 » et, à la fin, les mots : « de ce II » sont remplacés par les mots : « du II de l’article L. 254‑1 » ;

– au 3°, les mots : « mentionnée, d’une part, au 3° du II de l’article L. 254‑1 et, d’autre part, aux 1° ou 2° du même II ou au IV du même article » sont remplacés par les mots : « , d’une part, mentionnée au 3° du II de l’article L. 254‑1 et, d’autre part, de producteur au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 » ;

b) Le II est ainsi modifié :

– à la fin du 1°, les mots : « mentionnée aux 1° ou 2° du même II ou IV du même article » sont remplacés par les mots : « de producteur au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 » ;

– au 2°, les mots : « mentionnée aux 1° ou 2° du II ou au IV de l’article L. 254‑1 » sont remplacés par les mots : « de producteur au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 » et, à la fin, les mots : « de ce II » sont remplacés par les mots : « du II de l’article L. 254‑1 » ;

3° ter (nouveau) L’article L. 254‑1‑2 est ainsi modifié :

a) Le premier alinéa est ainsi modifié :

– les mots : « mentionnée aux 1° ou 2° du même II ou au IV du même article » sont remplacés par les mots : « de producteur au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 du 21 octobre 2009 » ;

– les mots : « mentionnée aux 1° ou 2° de ce II ou à ce IV de ce même article » sont remplacés par les mots : « de producteur au sens du même 11 » ;

– à la fin, les mots : « de ce II » sont remplacés par les mots : « du II de l’article L. 254‑1 » ;

b) Le second alinéa est supprimé ;

3° quater (nouveau) L’article L. 254‑1‑3 est ainsi modifié :

a) À la fin du I, les mots : « mentionnée aux 1° ou 2° du même II ou IV de ce même article » sont remplacés par les mots : « de producteur au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 du 21 octobre 2009 » ;
---
# 4

b) À la fin du II, les mots : « les activités mentionnées aux 1° ou 2° du même II ou au IV du même article » sont remplacés par les mots : « une activité de producteur au sens du 11 de l’article 3 du règlement (CE) n° 1107/2009 » ;

et 4° Au cinquième alinéa du I de l’article L. 254‑2, les mots : « aux 1° 2° du II de l’article L. 254‑1 » sont remplacés par les mots : « au 1° du II de l’article L. 254‑1 » ;

5° (Supprimé)

5° bis (nouveau) Les articles L. 254‑6‑2 et L. 254‑6‑3 sont abrogés ;

5° ter (nouveau) L’article L. 254‑6‑4 est ainsi modifié :

a) Le premier alinéa est ainsi modifié :

« – la première phrase est remplacée par quatre phrases ainsi rédigées :

I. – Le conseil mentionné au 3° du II de l’article L. 254‑1 couvre toute recommandation d’utilisation de produits phytopharmaceutiques. Il est formalisé par écrit. La prestation est effectuée à titre onéreux. Il s’inscrit dans un objectif de réduction de l’usage et des impacts des produits phytopharmaceutiques et respecte les principes généraux de la lutte intégrée contre les ennemis des cultures mentionnée à l’article L. 253‑6. » ;

– à la deuxième phrase, les mots : « ils privilégient » sont remplacés par les mots : « il privilégie » et les mots : « ils recommandent » sont remplacés par les mots : « il recommande » ;

– au début de la troisième phrase, les mots : « Ils promeuvent » sont remplacés par les mots : « Il promeut » ;

– au début de la dernière phrase, les mots : « Ils tiennent » sont remplacés par les mots : « Il tient » ;
---
# 5

b) Il est ajouté un II ainsi rédigé :

« II. – Le conseil stratégique à l’utilisation de produits phytopharmaceutiques établit un plan d’action pluriannuel pour la protection des cultures de l’exploitation agricole qui s’inscrit dans les objectifs du plan d’action national mentionné à l’article L. 253‑6. Il est fondé sur un diagnostic prenant en compte les spécificités de l’exploitation. Les exigences concernant la prévention des conflits d’intérêts pour la délivrance du conseil stratégique par le détenteur d’un agrément au titre des activités mentionnées au 1° du II de l’article L. 254‑1 sont déterminées par voie réglementaire. »

6° L’article L. 254‑7‑1 est ainsi modifié :

- a) (nouveau) Au premier alinéa, les mots : « , et notamment la désignation de l’autorité administrative, les conditions de délivrance, de renouvellement, de suspension, de modulation et de retrait des agréments, des certificats ainsi que des habilitations des organismes » sont supprimés ;
- b) Le second alinéa est ainsi modifié :

« – à la première phrase, après le mot : « prévoit », il est inséré le mot : notamment » ;

– la dernière phrase est ainsi rédigée : « Il précise les modalités de délivrance du conseil mentionné au 3° du II de l’article L. 254‑1. »

6° bis (nouveau) L’article L. 254‑10‑1 est ainsi modifié :

« a) À la fin de la première phrase du premier alinéa du I, les mots : auprès desquelles la redevance pour pollutions diffuses est exigible, mentionnées au IV de l’article L. 213‑10‑8 du code de l’environnement sont remplacés par les mots : « exerçant les activités mentionnées au 1° du II de l’article L. 254‑1 » ;

b) Au début du premier alinéa du II, les mots : « L’autorité administrative notifie à chaque obligé pour les périodes du 1ᵉʳ janvier 2020 au 31 décembre 2020 et du 1ᵉʳ janvier 2021 au 31 décembre 2021, puis, à compter du 1ᵉʳ janvier 2022, pour chaque période successive d’une durée fixée par décret en Conseil d’État, dans la limite de quatre ans » sont remplacés par les mots : « L’autorité administrative notifie à chaque obligé, pour chaque période successive » ;

6° ter (nouveau) Au premier alinéa du I de l’article L. 254‑12, le nombre : « 15 000 » est remplacé par le nombre : « 50 000 » ;
---
# 7° (nouveau)

Avant le titre Iᵉʳ du livre V, il est ajouté un titre préliminaire ainsi rédigé :

# TITRE PRÉLIMINAIRE

# DU CONSEIL STRATÉGIQUE GLOBAL

Art. L. 500-1. – I. – Les exploitants agricoles peuvent bénéficier d’un conseil stratégique global, formalisé par écrit, fourni par des conseillers compétents en agronomie, en protection des végétaux, en utilisation efficace, économe et durable des ressources ou en stratégie de valorisation et de filière, afin d’améliorer la viabilité économique, environnementale et sociale de leur exploitation.

Le conseil stratégique à l’utilisation de produits phytopharmaceutiques mentionné à l’article L. 254‑6‑4 constitue un volet de ce conseil stratégique global.

II. – Un décret définit les exigences relatives à l’exercice de la fonction de conseiller mentionnée au I, notamment en matière de formation.
```

### Output

The same paragraph, but with all references replaced by the actual text content they point to, resulting in an interpretable version with no ambiguity or external lookup required.

---

## 🧠 Challenges

- **Reference granularity**: references can be nested (e.g. "1° du II de L. 254‑1")
- **Recursion**: the referenced texts may contain further references
- **Multiple sources**: French codes, EU regulations, decrees, ANSES lists…
- **Text structure**: references are often implicit, abbreviated, or embedded

---

## 🛠️ Step-by-Step Breakdown

### Step 1: Reference Detection

Parse the paragraph and extract **all** normative references:

- Articles from French codes (e.g. L. 254‑1, L. 253‑5)
- Points and subparagraphs (e.g. 1° du II)
- European texts (e.g. article 3 du règlement (CE) n° 1107/2009)
- Technical lists (e.g. la liste mentionnée à l'article L. 253‑5)
- Regulatory texts (e.g. décret en Conseil d'État)

### Step 2: Reference Classification

For each reference, identify its source:

- Code rural et de la pêche maritime
- Code de l'environnement
- EU Regulation 1107/2009
- Décret, arrêté, or technical guidance

### Step 3: Text Retrieval

- Fetch the full text of each referenced item from an authoritative source (e.g. Legifrance, EUR-Lex), probably through LegiFrance api (https://piste.gouv.fr/index.php?option=com_apiportal&view=apitester&usage=api&apitab=tests&apiName=L%C3%A9gifrance&apiId=7e5a0e1d-ffcc-40be-a405-a1a5c1afe950&managerId=3&type=rest&apiVersion=2.4.2&Itemid=202&swaggerVersion=2.0&lang=en)
- Extract the precise subpart referenced (e.g. only the II 1° of an article)

### Step 4: Recursive Resolution

If the retrieved text contains further references:

- Apply the same process recursively
- Avoid infinite loops or circular references
- Track the reference tree

### Step 5: Substitution & Consolidation

- Replace the original references in the paragraph with their fully resolved contents
- Optionally, return both:
  - The flattened text, and
  - The reference tree for traceability

---

## 📦 Deliverables

A module that takes one paragraph and returns:

- A flattened, self-contained version of the text
- A metadata object showing all resolved references and their paths

---

## 🔄 Future Extensions

- Add support for multilingual text sources (EU texts in FR/EN)
- Track legal versioning and date-sensitive definitions
- Integrate with semantic parsers for full article modeling
