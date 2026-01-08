import os
import unittest

from google import genai
from google.genai import errors


class TestGeminiUpload(unittest.TestCase):
    def setUp(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            self.skipTest("GEMINI_API_KEY not set")
        self.client = genai.Client(api_key=self.api_key)

    def test_upload_file(self):
        # Create a dummy file
        filename = "test_gemini_upload.txt"
        with open(filename, "w") as f:
            f.write("Hello Gemini, this is a test file.")

        try:
            # Upload the file
            print(f"Uploading {filename}...")
            # SDK docs: client.files.upload(file='sample.txt', config={'name': 'display_file_name'})
            # But 'name' in config seems to be for display? No, config={'display_name': '...'} in some examples, {'name': '...'} in others.
            # Only 'display_name' is user-visible. 'name' is usually ID.
            # Docs say: config={'name': 'display_file_name'} in one snippet, but confusing.
            # Let's check the snippet from Chunk 9: client.files.upload(file='sample.txt', config={'name': 'display_file_name'})
            # Actually, standard is usually just path. Let's try simple upload.

            # Using keyword arguments as per new SDK style
            sample_file = self.client.files.upload(file=filename, config={"display_name": "Test File"})

            print(f"Uploaded file '{sample_file.display_name}' as: {sample_file.uri}")

            # Verify the file exists
            file = self.client.files.get(name=sample_file.name)
            self.assertEqual(file.display_name, "Test File")
            self.assertIn(file.state, ["PROCESSING", "ACTIVE"])

            # Verify file in list_files
            print("Listing files...")
            files = list(self.client.files.list())
            self.assertTrue(any(f.name == sample_file.name for f in files))
            print(f"File {sample_file.name} found in list_files")

            print(f"File state: {file.state}")

            # Clean up
            print(f"Deleting file {sample_file.name}...")
            self.client.files.delete(name=sample_file.name)

            # Verify deletion
            # New SDK might raise different exception or HttpError.
            # Common pattern is google.genai.errors.ClientError or similar?
            # Or just verify it's gone by checking list or get raises exception.
            # Let's import the specific exception if we can find it, otherwise using generic exception for now.
            # The previous SDK raised google.api_core.exceptions.NotFound
            # The new SDK is built differently.

            with self.assertRaises(errors.ClientError):
                self.client.files.get(name=sample_file.name)

        finally:
            # Clean up local file
            if os.path.exists(filename):
                os.remove(filename)


if __name__ == "__main__":
    unittest.main()
