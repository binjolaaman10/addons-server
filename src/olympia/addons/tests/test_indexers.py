# -*- coding: utf-8 -*-
from itertools import chain

from olympia.amo.models import SearchMixin
from olympia.amo.tests import ESTestCase, TestCase
from olympia.addons.models import (
    Addon, attach_categories, attach_tags, attach_translations)
from olympia.addons.indexers import AddonIndexer
from olympia.constants.search import SEARCH_ANALYZER_MAP


class TestAddonIndexer(TestCase):
    fixtures = ['base/users', 'base/addon_3615']

    # The base list of fields we expect to see in the mapping/extraction.
    # This only contains the fields for which we use the value directly,
    # see expected_fields() for the rest.
    simple_fields = [
        'id', 'slug', 'created', 'default_locale', 'last_updated',
        'weekly_downloads', 'average_daily_users', 'status', 'type',
        'hotness', 'is_disabled', 'is_listed',
    ]

    def setUp(self):
        super(TestAddonIndexer, self).setUp()
        self.transforms = (attach_categories, attach_tags, attach_translations)
        self.indexer = AddonIndexer()

    @classmethod
    def expected_fields(cls):
        """
        Returns a list of fields we expect to be present in the mapping and
        in the extraction method.

        Should be updated whenever you change the mapping to add/remove fields.
        """
        # Fields that can not be directly compared with the property of the
        # same name on the Addon instance, either because the property doesn't
        # exist on the model, or it has a different name, or the value we need
        # to store in ES differs from the one in the db.
        complex_fields = [
            'app', 'appversion', 'authors', 'bayesian_rating', 'boost',
            'category', 'description', 'has_theme_rereview', 'has_version',
            'name', 'name_sort', 'platforms', 'summary', 'tags',
        ]

        # For each translated field that needs to be indexed, we store one
        # version for each language-specific analyzer we have.
        _indexed_translated_fields = ('name', 'description', 'summary')
        analyzer_fields = list(chain.from_iterable(
            [['%s_%s' % (field, analyzer) for analyzer in SEARCH_ANALYZER_MAP]
             for field in _indexed_translated_fields]))

        # It'd be annoying to hardcode `analyzer_fields`, so we generate it,
        # but to make sure the test is correct we still do a simple check of
        # the length to make sure we properly flattened the list.
        assert len(analyzer_fields) == (len(SEARCH_ANALYZER_MAP) *
                                        len(_indexed_translated_fields))

        # Each translated field that we want to return to the API.
        raw_translated_fields = [
            '%s_translations' % field for field in
            ['name', 'description', 'homepage', 'summary', 'support_email',
             'support_url']]

        # Return a list with the base fields and the dynamic ones added.
        return (cls.simple_fields + complex_fields + analyzer_fields +
                raw_translated_fields)

    def test_mapping(self):
        doc_name = self.indexer.get_doctype_name()
        assert doc_name

        mapping_properties = self.indexer.get_mapping()[doc_name]['properties']

        # Make sure the get_mapping() method does not return fields we did
        # not expect to be present, or omitted fields we want.
        assert set(mapping_properties.keys()) == set(self.expected_fields())

        # Make sure default_locale and translated fields are not indexed.
        assert mapping_properties['default_locale']['index'] == 'no'
        name_translations = mapping_properties['name_translations']
        assert name_translations['properties']['lang']['index'] == 'no'
        assert name_translations['properties']['string']['index'] == 'no'

    def _extract(self):
        qs = Addon.objects.filter(id__in=[3615])
        for t in self.transforms:
            qs = qs.transform(t)
        self.addon = list(qs)[0]
        return self.indexer.extract_document(self.addon)

    def test_extract_attributes(self):
        extracted = self._extract()

        # Like test_mapping() above, but for the extraction process:
        # Make sure the method does not return fields we did not expect to be
        # present, or omitted fields we want.
        assert set(extracted.keys()) == set(self.expected_fields())

        # Check base fields values. Other tests below check the dynamic ones.
        for field_name in self.simple_fields:
            assert extracted[field_name] == getattr(self.addon, field_name)

    def test_extract_translations(self):
        translations_name = {
            'en-US': u'Name in ënglish',
            'es': u'Name in Español',
            'it': None,  # Empty name should be ignored in extract.
        }
        translations_description = {
            'en-US': u'Description in ënglish',
            'es': u'Description in Español',
            'fr': '',  # Empty description should be ignored in extract.
        }
        self.addon = Addon.objects.get(pk=3615)
        self.addon.name = translations_name
        self.addon.description = translations_description
        self.addon.save()
        extracted = self._extract()
        assert extracted['name_translations'] == [
            {'lang': u'en-US', 'string': translations_name['en-US']},
            {'lang': u'es', 'string': translations_name['es']},
        ]
        assert extracted['description_translations'] == [
            {'lang': u'en-US', 'string': translations_description['en-US']},
            {'lang': u'es', 'string': translations_description['es']},
        ]
        assert extracted['name_english'] == [translations_name['en-US']]
        assert extracted['name_spanish'] == [translations_name['es']]
        assert (extracted['description_english'] ==
                [translations_description['en-US']])
        assert (extracted['description_spanish'] ==
                [translations_description['es']])


class TestAddonIndexerWithES(ESTestCase):
    fixtures = ['base/users', 'base/addon_3615']

    def test_mapping(self):
        """Compare actual mapping in ES with the one the indexer returns, once
        an object has been indexed.

        We don't want dynamic mapping for addons (too risky), so the two
        mappings should be equal."""
        self.reindex(Addon)

        indexer = AddonIndexer()
        doc_name = indexer.get_doctype_name()
        real_index_name = self.index_names[SearchMixin.ES_ALIAS_KEY]
        mappings = self.es.indices.get_mapping(
            indexer.get_index_alias())[real_index_name]['mappings']

        actual_properties = mappings[doc_name]['properties']
        indexer_properties = indexer.get_mapping()[doc_name]['properties']

        assert set(actual_properties.keys()) == set(indexer_properties.keys())
