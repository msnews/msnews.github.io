# Introduction to MIND-small dataset

## Overall Introduction

The MIND dataset for news recommendation was collected from the user behavior logs of Microsoft News.
We randomly sampled 1 million users who had at least 5 news clicks during 6 weeks from October 12 to November 22, 2019.
To protect user privacy, each user is de-linked from the production system when securely hashed into an anonymized ID.
We collected the news click behaviors of these users in this period, which are formatted into impression logs.
We used the impression logs in the last week for test, and the logs in the fifth week for training.
For samples in training set, we used the click behaviors in the first four weeks to construct the news click history for user modeling.
Among the training data, we used the samples in the last day of the fifth week as validation set.
The complete MIND dataset will be released during the ACL 2020 conference.
Currently we release a small version of MIND (**MIND-small**), by randomly sampling 50,000 users and their behavior logs.
Only training and validation sets are contained in the MIND-small dataset.

## Dataset Format

Both the training and validation data are a zip-compressed folder, which contains four different files:

File Name | Description
------------- | -------------
behaviors.tsv  | The click histories and impression logs of users
news.tsv  | The information of news articles
entity_embedding.vec    | The embeddings of entities in news extracted from knowledge graph
relation_embedding.vec    | The embeddings of relations between entities extracted from knowledge graph


### behaviors.tsv

The behaviors.tsv file contains the impression logs and users' news click hostories. 
It has 3 columns divided by the tab symbol ("\t"):

* User ID. The hashed ID of a user.
* Time. The impression time with format "MM/DD/YYYY HH:MM:SS AM/PM".
* History. The news click history (ID list of clicked news) of this user before this impression. 
* Impressions. List of news displayed in this impression and user's click behaviors on them (1 for click and 0 for non-click).

An example is shown in the table below:

Column | Content
------------- | -------------
User ID | U131
Time | 11/13/2019 8:36:57 AM
History | N11 N21 N103
Impressions | N4-1 N34-1 N156-0 N207-0 N198-0
 
### news.tsv

The docs.tsv file contains the detailed information of news articles involved in the behaviors.tsv file.
It has 7 columns, which are divided by the tab symbol:

* News ID 
* Category 
* SubCategory
* Title
* Abstract
* URL
* Entities (entities contained in the text of this news)

Due to the policy of news publishers, we cannot directly provide the body of the news article.
If you need, you can crawl the news body from the news URL.
We wrote a [crawler script](https://github.com/msnews/MIND/tree/master/crawler) to help you crawl and parse the news webpage.

An example is shown in the following table:

Column | Content
------------- | -------------
News ID | N94289
Category | health
SubCategory | weightloss
Title | 50 Worst Habits For Belly Fat
Abstract | These seemingly harmless habits are holding you back and keeping you from shedding that unwanted belly fat for good.
URL | https://www.msn.com/en-us/health/weightloss/50-worst-habits-for-belly-fat/ss-AAB19MK?ocid=chopendata
Entities | [{"Label": "Adipose tissue", "Type": "C", "WikidataId": "Q193583", "Confidence": 1.0, "OccurrenceOffsets": [22, 130], "SurfaceForms": ["Belly Fat", "belly fat"]}]

The descriptions of the dictionary keys in the "Entities" column are listed as follows:
Keys | Description
------------- | -------------
Label | The entity name in the Wikidata knwoledge graph
Type | The type of this entity in Wikidata
WikidataId | The entity ID in Wikidata
Confidence | The confidence of entity linking
OccurrenceOffsets | The character-level entity offset in the concatenation of news title, abstract and body
SurfaceForms | The raw entity names in the original text


 

### entity_embedding.vec & relation_embedding.vec 
The entity_embedding.vec and relation_embedding.vec files contain the 100-dimensional embeddings of the entities and relations learned from WikiData knowledge graph by TransE method.
In both files, the first column is the ID of entity/relation, and the other columns are the embedding vector values.
We hope this data can facilitate the research of knowledge-aware news recommendation.
An example is shown as follows:
ID | Embedding Values
------------- | -------------
Q42306013 | 0.014516	-0.106958	0.024590	...	-0.080382

