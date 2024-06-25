"""
Test the import_staged_content_from_user_clipboard() method, which is used to
allow users to paste XBlocks that were copied using the staged_content/clipboard
APIs.
"""
import ddt
from django.test import LiveServerTestCase
from opaque_keys.edx.keys import UsageKey
from rest_framework.test import APIClient
from xmodule.modulestore.django import contentstore, modulestore
from xmodule.modulestore.tests.django_utils import ModuleStoreTestCase, upload_file_to_course
from xmodule.modulestore.tests.factories import BlockFactory, CourseFactory, LibraryFactory, ToyCourseFactory

from cms.djangoapps.contentstore.utils import reverse_usage_url
from cms.djangoapps.contentstore.tests.utils import AjaxEnabledTestClient

CLIPBOARD_ENDPOINT = "/api/content-staging/v1/clipboard/"
XBLOCK_ENDPOINT = "/xblock/"


@ddt.ddt
class ClipboardPasteTestCase(ModuleStoreTestCase):
    """
    Test Clipboard Paste functionality
    """

    def _setup_course(self):
        """ Set up the "Toy Course" and an APIClient for testing clipboard functionality. """
        # Setup:
        course_key = ToyCourseFactory.create().id  # See xmodule/modulestore/tests/sample_courses.py
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)
        return (course_key, client)

    def test_copy_and_paste_video(self):
        """
        Test copying a video from the course, and pasting it into the same unit
        """
        course_key, client = self._setup_course()

        # Check how many blocks are in the vertical currently
        parent_key = course_key.make_usage_key("vertical", "vertical_test")  # This is the vertical that holds the video
        orig_vertical = self.store.get_item(parent_key)
        assert len(orig_vertical.children) == 4

        # Copy the video
        video_key = course_key.make_usage_key("video", "sample_video")
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(video_key)}, format="json")
        assert copy_response.status_code == 200

        # Paste the video
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(parent_key),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        new_block_key = UsageKey.from_string(paste_response.json()["locator"])

        # Now there should be an extra block in the vertical:
        updated_vertical = self.store.get_item(parent_key)
        assert len(updated_vertical.children) == 5
        assert updated_vertical.children[-1] == new_block_key
        # And it should match the original:
        orig_video = self.store.get_item(video_key)
        new_video = self.store.get_item(new_block_key)
        assert new_video.youtube_id_1_0 == orig_video.youtube_id_1_0
        # The new block should store a reference to where it was copied from
        assert new_video.copied_from_block == str(video_key)

    def test_copy_and_paste_unit(self):
        """
        Test copying a unit (vertical) from one course into another
        """
        course_key, client = self._setup_course()
        dest_course = CourseFactory.create(display_name='Destination Course')
        with self.store.bulk_operations(dest_course.id):
            dest_chapter = BlockFactory.create(parent=dest_course, category='chapter', display_name='Section')
            dest_sequential = BlockFactory.create(parent=dest_chapter, category='sequential', display_name='Subsection')

        # Copy the unit
        unit_key = course_key.make_usage_key("vertical", "vertical_test")
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(unit_key)}, format="json")
        assert copy_response.status_code == 200

        # Paste the unit
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(dest_sequential.location),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        dest_unit_key = UsageKey.from_string(paste_response.json()["locator"])

        # Now there should be a one unit/vertical as a child of the destination sequential/subsection:
        updated_sequential = self.store.get_item(dest_sequential.location)
        assert updated_sequential.children == [dest_unit_key]
        # And it should match the original:
        orig_unit = self.store.get_item(unit_key)
        dest_unit = self.store.get_item(dest_unit_key)
        assert len(orig_unit.children) == len(dest_unit.children)
        # Check details of the fourth child (a poll)
        orig_poll = self.store.get_item(orig_unit.children[3])
        dest_poll = self.store.get_item(dest_unit.children[3])
        assert dest_poll.display_name == orig_poll.display_name
        assert dest_poll.question == orig_poll.question
        # The new block should store a reference to where it was copied from
        assert dest_unit.copied_from_block == str(unit_key)

    @ddt.data(
        # A problem with absolutely no fields set. A previous version of copy-paste had an error when pasting this.
        {"category": "problem", "display_name": None, "data": ""},
    )
    def test_copy_and_paste_component(self, block_args):
        """
        Test copying a component (XBlock) from one course into another
        """
        source_course = CourseFactory.create(display_name='Source Course')
        source_block = BlockFactory.create(parent_location=source_course.location, **block_args)

        dest_course = CourseFactory.create(display_name='Destination Course')
        with self.store.bulk_operations(dest_course.id):
            dest_chapter = BlockFactory.create(parent=dest_course, category='chapter', display_name='Section')
            dest_sequential = BlockFactory.create(parent=dest_chapter, category='sequential', display_name='Subsection')

        # Copy the block
        client = APIClient()
        client.login(username=self.user.username, password=self.user_password)
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(source_block.location)}, format="json")
        assert copy_response.status_code == 200

        # Paste the unit
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(dest_sequential.location),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        dest_block_key = UsageKey.from_string(paste_response.json()["locator"])

        dest_block = self.store.get_item(dest_block_key)
        assert dest_block.display_name == source_block.display_name
        # The new block should store a reference to where it was copied from
        assert dest_block.copied_from_block == str(source_block.location)

    def test_paste_with_assets(self):
        """
        When pasting into a different course, any required static assets should
        be pasted too, unless they already exist in the destination course.
        """
        dest_course_key, client = self._setup_course()
        # Make sure some files exist in the source course to be copied:
        source_course = CourseFactory.create()
        upload_file_to_course(
            course_key=source_course.id,
            contentstore=contentstore(),
            source_file='./common/test/data/static/picture1.jpg',
            target_filename="picture1.jpg",
        )
        upload_file_to_course(
            course_key=source_course.id,
            contentstore=contentstore(),
            source_file='./common/test/data/static/picture2.jpg',
            target_filename="picture2.jpg",
        )
        source_html = BlockFactory.create(
            parent_location=source_course.location,
            category="html",
            display_name="Some HTML",
            data="""
            <p>
                <a href="/static/picture1.jpg">Picture 1</a>
                <a href="/static/picture2.jpg">Picture 2</a>
            </p>
            """,
        )

        # Now, to test conflict handling, we also upload a CONFLICTING image to
        # the destination course under the same filename.
        upload_file_to_course(
            course_key=dest_course_key,
            contentstore=contentstore(),
            # Note this is picture 3, not picture 2, but we save it as picture 2:
            source_file='./common/test/data/static/picture3.jpg',
            target_filename="picture2.jpg",
        )

        # Now copy the HTML block from the source cost and paste it into the destination:
        copy_response = client.post(CLIPBOARD_ENDPOINT, {"usage_key": str(source_html.location)}, format="json")
        assert copy_response.status_code == 200

        # Paste the video
        dest_parent_key = dest_course_key.make_usage_key("vertical", "vertical_test")
        paste_response = client.post(XBLOCK_ENDPOINT, {
            "parent_locator": str(dest_parent_key),
            "staged_content": "clipboard",
        }, format="json")
        assert paste_response.status_code == 200
        static_file_notices = paste_response.json()["static_file_notices"]
        assert static_file_notices == {
            "error_files": [],
            "new_files": ["picture1.jpg"],
            # The new course already had a file named "picture2.jpg" with different md5 hash, so it's a conflict:
            "conflicting_files": ["picture2.jpg"],
        }

        # Check that the files are as we expect:
        source_pic1_hash = contentstore().find(source_course.id.make_asset_key("asset", "picture1.jpg")).content_digest
        dest_pic1_hash = contentstore().find(dest_course_key.make_asset_key("asset", "picture1.jpg")).content_digest
        assert source_pic1_hash == dest_pic1_hash
        source_pic2_hash = contentstore().find(source_course.id.make_asset_key("asset", "picture2.jpg")).content_digest
        dest_pic2_hash = contentstore().find(dest_course_key.make_asset_key("asset", "picture2.jpg")).content_digest
        assert source_pic2_hash != dest_pic2_hash  # Because there was a conflict, this file was unchanged.


class ClipboardLibraryContentPasteTestCase(LiveServerTestCase, ModuleStoreTestCase):
    """
    Test Clipboard Paste functionality with library content
    """

    def setUp(self):
        """
        Set up a v2 Content Library and a library content block
        """
        super().setUp()
        self.client = AjaxEnabledTestClient()
        self.client.login(username=self.user.username, password=self.user_password)
        self.store = modulestore()

    def test_paste_library_content_block_v1(self):
        """
        Same as the above test, but uses modulestore (v1) content library
        """
        library = LibraryFactory.create()
        data = {
            'parent_locator': str(library.location),
            'category': 'html',
            'display_name': 'HTML Content',
        }
        response = self.client.ajax_post(XBLOCK_ENDPOINT, data)
        self.assertEqual(response.status_code, 200)
        course = CourseFactory.create(display_name='Course')
        orig_lc_block = BlockFactory.create(
            parent=course,
            category="library_content",
            source_library_id=str(library.location.library_key),
            display_name="LC Block",
            publish_item=False,
        )
        orig_lc_block.refresh_children()
        orig_child = self.store.get_item(orig_lc_block.children[0])
        assert orig_child.display_name == "HTML Content"
        # Copy a library content block that has children:
        copy_response = self.client.post(CLIPBOARD_ENDPOINT, {
            "usage_key": str(orig_lc_block.location)
        }, format="json")
        assert copy_response.status_code == 200

        # Paste the Library content block:
        paste_response = self.client.ajax_post(XBLOCK_ENDPOINT, {
            "parent_locator": str(course.location),
            "staged_content": "clipboard",
        })
        assert paste_response.status_code == 200
        dest_lc_block_key = UsageKey.from_string(paste_response.json()["locator"])

        # Get the ID of the new child:
        dest_lc_block = self.store.get_item(dest_lc_block_key)
        dest_child = self.store.get_item(dest_lc_block.children[0])
        assert dest_child.display_name == "HTML Content"

        # Importantly, the ID of the child must not changed when the library content is synced.
        # Otherwise, user state saved against this child will be lost when it syncs.
        dest_lc_block.refresh_children()
        updated_dest_child = self.store.get_item(dest_lc_block.children[0])
        assert dest_child.location == updated_dest_child.location

    def _sync_lc_block_from_library(self, attr_name):
        """
        Helper method to "sync" a Library Content Block by [re-]fetching its
        children from the library.
        """
        usage_key = getattr(self, attr_name).location
        # It's easiest to do this via the REST API:
        handler_url = reverse_usage_url('preview_handler', usage_key, kwargs={'handler': 'upgrade_and_sync'})
        response = self.client.post(handler_url)
        assert response.status_code == 200
        # Now reload the block and make sure the child is in place
        setattr(self, attr_name, self.store.get_item(usage_key))  # we must reload after upgrade_and_sync
