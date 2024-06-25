"""
Test for auto-tagging content
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import override_settings
from edx_toggles.toggles.testutils import override_waffle_flag
from openedx_tagging.core.tagging.models import Tag, Taxonomy
from organizations.models import Organization

from common.djangoapps.student.tests.factories import UserFactory
from openedx.core.djangolib.testing.utils import skip_unless_cms
from xmodule.modulestore.tests.django_utils import TEST_DATA_SPLIT_MODULESTORE, ModuleStoreTestCase

from .. import api
from ..models.base import TaxonomyOrg
from ..toggles import CONTENT_TAGGING_AUTO
from ..types import ContentKey

LANGUAGE_TAXONOMY_ID = -1


class LanguageTaxonomyTestMixin:
    """
    Mixin for test cases that expect the Language System Taxonomy to exist.
    """

    def setUp(self):
        """
        When pytest runs, it creates the database by inspecting models, not by
        running migrations. So data created by our migrations is not present.
        In particular, the Language Taxonomy is not present. So this mixin will
        create the taxonomy, simulating the effect of the following migrations:
            1. openedx_tagging.core.tagging.migrations.0012_language_taxonomy
            2. content_tagging.migrations.0007_system_defined_org_2
        """
        super().setUp()
        Taxonomy.objects.get_or_create(id=-1, defaults={
            "name": "Languages",
            "description": "Languages that are enabled on this system.",
            "enabled": True,
            "allow_multiple": False,
            "allow_free_text": False,
            "visible_to_authors": True,
            "_taxonomy_class": "openedx_tagging.core.tagging.models.system_defined.LanguageTaxonomy",
        })
        TaxonomyOrg.objects.get_or_create(taxonomy_id=-1, defaults={"org": None})


@skip_unless_cms  # Auto-tagging is only available in the CMS
@override_waffle_flag(CONTENT_TAGGING_AUTO, active=True)
class TestAutoTagging(LanguageTaxonomyTestMixin, ModuleStoreTestCase):
    """
    Test if the Course and XBlock tags are automatically created
    """

    MODULESTORE = TEST_DATA_SPLIT_MODULESTORE

    def _check_tag(self, object_key: ContentKey, taxonomy_id: int, value: str | None):
        """
        Check if the ObjectTag exists for the given object_id and taxonomy_id

        If value is None, check if the ObjectTag does not exists
        """
        object_tags = api.get_content_tags(object_key, taxonomy_id=taxonomy_id)
        object_tag = object_tags[0] if len(object_tags) == 1 else None
        if len(object_tags) > 1:
            raise ValueError("Found too many object tags")
        if value is None:
            assert not object_tag, f"Expected no tag for taxonomy_id={taxonomy_id}, " \
                f"but one found with value={object_tag.value}"
        else:
            assert object_tag, f"Tag for taxonomy_id={taxonomy_id} with value={value} with expected, but none found"
            assert object_tag.value == value, f"Tag value mismatch {object_tag.value} != {value}"

        return True

    def setUp(self):
        super().setUp()
        # Create user
        self.user = UserFactory.create()
        self.user_id = self.user.id

        self.orgA = Organization.objects.create(name="Organization A", short_name="orgA")
        self.patcher = patch("openedx.core.djangoapps.content_tagging.tasks.modulestore", return_value=self.store)
        self.addCleanup(self.patcher.stop)
        self.patcher.start()

    def test_create_course(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pl"},
        )

        # Check if the tags are created in the Course
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Polski")

    @override_settings(LANGUAGE_CODE='pt-br')
    def test_create_course_invalid_language(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "11"},
        )

        # Check if the tags are created in the Course is the system default
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Português (Brasil)")

    @override_settings(LANGUAGES=[('pt', 'Portuguese')], LANGUAGE_DICT={'pt': 'Portuguese'}, LANGUAGE_CODE='pt')
    def test_create_course_unsuported_language(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "en"},
        )

        # Check if the tags are created in the Course is the system default
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Portuguese")

    @override_settings(LANGUAGE_CODE='pt')
    def test_create_course_no_tag_default_language(self):
        # Remove Portuguese tag
        Tag.objects.filter(taxonomy_id=LANGUAGE_TAXONOMY_ID, value="Portuguese").delete()
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "11"},
        )

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

    def test_update_course(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt-br"},
        )

        # Simulates user manually changing a tag
        lang_taxonomy = Taxonomy.objects.get(pk=LANGUAGE_TAXONOMY_ID)
        api.tag_content_object(course.id, lang_taxonomy, ["Español (España)"])

        # Update course language
        course.language = "en"
        self.store.update_item(course, self.user_id)

        # Does not automatically update the tag
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, "Español (España)")

    def test_create_delete_xblock(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt-br"},
        )

        # Create XBlocks
        sequential = self.store.create_child(self.user_id, course.location, "sequential", "test_sequential")
        vertical = self.store.create_child(self.user_id, sequential.location, "vertical", "test_vertical")

        usage_key_str = str(vertical.location)

        # Check if the tags are created in the XBlock
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, "Português (Brasil)")

        # Delete the XBlock
        self.store.delete_item(vertical.location, self.user_id)

        # Check if the tags are deleted
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, None)

    @override_waffle_flag(CONTENT_TAGGING_AUTO, active=False)
    def test_waffle_disabled_create_update_course(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt"},
        )

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

        # Update course language
        course.language = "en"
        self.store.update_item(course, self.user_id)

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

    @override_waffle_flag(CONTENT_TAGGING_AUTO, active=False)
    def test_waffle_disabled_create_delete_xblock(self):
        # Create course
        course = self.store.create_course(
            self.orgA.short_name,
            "test_course",
            "test_run",
            self.user_id,
            fields={"language": "pt"},
        )

        # Create XBlocks
        sequential = self.store.create_child(self.user_id, course.location, "sequential", "test_sequential")
        vertical = self.store.create_child(self.user_id, sequential.location, "vertical", "test_vertical")

        usage_key_str = str(vertical.location)

        # No tags created
        assert self._check_tag(course.id, LANGUAGE_TAXONOMY_ID, None)

        # Delete the XBlock
        self.store.delete_item(vertical.location, self.user_id)

        # Still no tags
        assert self._check_tag(usage_key_str, LANGUAGE_TAXONOMY_ID, None)
