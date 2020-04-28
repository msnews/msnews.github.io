# Introduction to MIND-small

<img src="https://msnews.github.io/assets/img/icons/logo.png" width = "320" height = "100" alt="MIND" align=center />

## Dataset Construction

The MIND dataset for news recommendation was collected from the user behavior logs of Microsoft News.
We randomly sampled 1 million users who had at least 5 news clicks during 6 weeks from October 12 to November 22, 2019.
To protect user privacy, each user is de-linked from the production system when securely hashed into an anonymized ID.
We collected the news click behaviors of these users in this period, which are formatted into impression logs.
We used the samples in the last week for test, and the samples in the fifth week for training.
For samples in training set, we used the click behaviors in the first four weeks to construct the news click history.
Among the training data, we used the samples in the last day of the fifth week as development set.
Currently we release a small version of MIND (**MIND-small**), which contains the logs of 5% of users (50k) in the full MIND dataset.

## Dataset Format

The training  or development data is a zip-compressed folder, which contains four different files:

File Name | Description
------------- | -------------
behaviors.tsv  | The click histories and impression logs of users
news.tsv  | The information of news articles
entity_embedding.vec    | The embeddings of entities in news extracted from knowledge graph
relation_embedding.vec    | The embeddings of relations between entities extracted from knowledge graph

The details of these files are introduced as follows.

### behaviors.tsv

The behaviors.tsv file records users' historical clicked news and impression logs. 
It contains 3 columns divided by the tab symbol ("\t"):

* User ID. The hashed ID of a user.
* History. This column contains the historical news click behaivors. The data format is "[News ID 1] [News ID 2] ... [News ID N]".
* Impressions. This column contains both clicked (positive) and non-clicked (negative) news in an impression log.
Its format is "[Positive News IDs]#TAB#[Negative News IDs]#TAB#[Impression Time]''.
For positive and negative news, their gold labels are 1 and 0, respectively. 

In these columns, different news ids are separated by whitespaces.
An example is shown in the table below:


Column | Content
------------- | -------------
User ID | 1 
History | AAILo3Y AAHTTnY AAIDF2D AAILEPP AAId0Jq AAIP9Iu AAIUBNg AAIUZLr AAJ77GF AAIZiRj AAJ44GF AAJ78cD AAJ7b2R AAJ7p5A AAJ8VW1 AAJjwCz AAJjPME AAJkGqm AAJjvBc AAJl72L AAJjm1I AAIokLY AAJsnPc AAJpzEv AAIT99q AAJsbD8 AAJhbUQ AAJvHE4 AAJuVER AAJwFwZ AAJy6rv AAJBsSa AAJBmut AAJAS2G AAJD04K AAJO1fS AAJOzRe AAJHU3j AAJMQY3 AAJPvxp AAJOB8z AAJOADo AAJQJWm AAJQHG7 AAJTZQY AAJWBzM AAJQK0Y AAJUClf AAJUAE8 AAJWgJA AAJT0gz AAJXnjy AAJY0GD
Impressions | BBWqeaV BBWEvuv#TAB#BBFu2Vk AAJWrdR BBWG130 BBWF0Kt BBWG0gw BBWEF0P BBWF3V2 BBWEm3k BBWGf4l BBWHiSI BBWGfty BBWGhtW BBWGxS0#TAB#11/13/2019 8:36:57 AM
 
### news.tsv

The docs.tsv file records the detailed information of news articles involved in the behaviors.tsv file.
It contains 7 columns, which are divided by the tab symbol:

* News ID 
* Vertical (the vertical category of a news article)
* Subvertical (a finer-grained subvertical category of a news article)
* Title
* Abstract
* URL
* Entities (a set of entities derived from the Wikidata knowledge graph)

An example is shown in the following table:

Column | Content
------------- | -------------
News ID | AAGH0ET
Vertical | lifestyle
Subvertical | lifestyleroyals
Title | The Brands Queen Elizabeth, Prince Charles, and Prince Philip Swear By Shop
Abstract | the notebooks, jackets, and more that the royals can't live without.
URL | https://www.msn.com/en-us/lifestyle/lifestyleroyals/the-brands-queen-elizabeth,-prince-charles,-and-prince-philip-swear-by/ss-AAGH0ET?ocid=chopendata
Entities | [{"Label": "Prince Philip, Duke of Edinburgh", "Type": "P", "WikidataId": "Q80976", "Confidence": 1.0, "OccurrenceOffsets": [50], "SurfaceForms": ["Prince Philip"]}, {"Label": "Business", "Type": "C", "WikidataId": "Q4830453", "Confidence": 0.775, "OccurrenceOffsets": [74], "SurfaceForms": ["Shop"]}, {"Label": "Charles, Prince of Wales", "Type": "P", "WikidataId": "Q43274", "Confidence": 1.0, "OccurrenceOffsets": [30], "SurfaceForms": ["Prince Charles"]}, {"Label": "Elizabeth II", "Type": "P", "WikidataId": "Q9682", "Confidence": 0.97, "OccurrenceOffsets": [13], "SurfaceForms": ["Queen Elizabeth"]}]

The descriptions of the dictionary keys in the "Entities" column are listed as follows:
Keys | Description
------------- | -------------
Label | The entity name in the knwoledge graph
Type | The type of entity
WikidataId | The entity ID in Wikidata
Confidence | The confidence of entity linking
OccurrenceOffsets | The character-level entity offset in the concatenation of news title, abstract and body
SurfaceForms | The raw entity in the original text

Due to the policy of news publishers, you need to crawl the news body (and may be other information) through the news URL.
We provide a [crawler script](https://github.com/msnews/MIND/tree/master/crawler) written in Python.
 

### entity_embedding.vec & relation_embedding.vec 
The entity_embedding.vec and relation_embedding.vec files contains the 100-dimensional embeddings of the entities and relations obtained by transE.
In both files, the first column is the ID of en entity/relation, and the other columns store its real-valued embedding.
An example is shown as follows:
ID | Embedding Values
------------- | -------------
Q42306013 | 0.014516	-0.106958	0.024590	...	-0.080382

