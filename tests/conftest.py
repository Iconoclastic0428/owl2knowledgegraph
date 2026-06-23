from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def foodon_like_owl(tmp_path: Path) -> Path:
    ontology = """<?xml version="1.0"?>
<rdf:RDF
    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
    xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
    xmlns:owl="http://www.w3.org/2002/07/owl#"
    xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
    xmlns:obo="http://purl.obolibrary.org/obo/"
    xmlns:test="http://example.org/foodon-test.owl#">

  <owl:Ontology rdf:about="http://example.org/foodon-test.owl"/>

  <owl:AnnotationProperty rdf:about="http://purl.obolibrary.org/obo/IAO_0000115">
    <rdfs:label>definition</rdfs:label>
  </owl:AnnotationProperty>

  <owl:ObjectProperty rdf:about="http://purl.obolibrary.org/obo/RO_0001000">
    <rdfs:label>derives from</rdfs:label>
    <rdfs:domain rdf:resource="http://purl.obolibrary.org/obo/FOODON_00002403"/>
    <rdfs:range rdf:resource="http://purl.obolibrary.org/obo/FOODON_00001002"/>
  </owl:ObjectProperty>

  <owl:DatatypeProperty rdf:about="http://example.org/foodon-test.owl#hasQuality">
    <rdfs:label>has quality</rdfs:label>
    <rdfs:domain rdf:resource="http://purl.obolibrary.org/obo/FOODON_00002403"/>
    <rdfs:range rdf:resource="http://www.w3.org/2001/XMLSchema#string"/>
  </owl:DatatypeProperty>

  <owl:Class rdf:about="http://purl.obolibrary.org/obo/FOODON_00001002">
    <rdfs:label>food material</rdfs:label>
    <obo:IAO_0000115>A material consumed as food.</obo:IAO_0000115>
  </owl:Class>

  <owl:Class rdf:about="http://purl.obolibrary.org/obo/FOODON_00002403">
    <rdfs:label>food product</rdfs:label>
    <rdfs:subClassOf rdf:resource="http://purl.obolibrary.org/obo/FOODON_00001002"/>
    <rdfs:subClassOf>
      <owl:Restriction>
        <owl:onProperty rdf:resource="http://purl.obolibrary.org/obo/RO_0001000"/>
        <owl:someValuesFrom rdf:resource="http://purl.obolibrary.org/obo/FOODON_00001002"/>
      </owl:Restriction>
    </rdfs:subClassOf>
  </owl:Class>

  <owl:NamedIndividual rdf:about="http://example.org/foodon-test.owl#sample_food">
    <rdf:type rdf:resource="http://purl.obolibrary.org/obo/FOODON_00002403"/>
    <obo:RO_0001000 rdf:resource="http://example.org/foodon-test.owl#sample_ingredient"/>
    <test:hasQuality rdf:datatype="http://www.w3.org/2001/XMLSchema#string">fresh</test:hasQuality>
  </owl:NamedIndividual>

  <owl:NamedIndividual rdf:about="http://example.org/foodon-test.owl#sample_ingredient">
    <rdf:type rdf:resource="http://purl.obolibrary.org/obo/FOODON_00001002"/>
  </owl:NamedIndividual>
</rdf:RDF>
"""
    path = tmp_path / "foodon_like.owl"
    path.write_text(ontology, encoding="utf-8")
    return path
