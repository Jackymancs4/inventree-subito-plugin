"""Sample implementation for ActionMixin."""

import io
import inspect, json, logging

from plugin import InvenTreePlugin
from plugin.mixins import ActionMixin, APICallMixin, SettingsMixin, EventMixin
from company.models import Company, SupplierPriceBreak
from part.models import (
    Part,
    SupplierPart,
    PartCategory,
    PartParameterTemplate,
    PartParameter,
    BomItem,
    BomItemSubstitute,
    PartAttachment,
)
from stock.models import StockItem
from InvenTree.helpers_model import download_image_from_url
from django.core.files.base import ContentFile
from InvenTree.tasks import offload_task

logger = logging.getLogger("subitoplugin")


class SubitoPlugin(ActionMixin, APICallMixin, SettingsMixin, InvenTreePlugin):
    """An action plugin which offers variuous integrations with Subito.it."""

    NAME = "SubitoPlugin"
    SLUG = "subito"
    ACTION_NAME = "subito"

    result = {}

    def import_image(self, url: str, part: PartAttachment) -> bool:

        if part.attachment:
            return False

        # URL can be empty (null), for example for stickers parts
        if not url:
            return False

        remote_img = download_image_from_url(url)

        if remote_img and part:
            fmt = remote_img.format or "PNG"
            buffer = io.BytesIO()
            remote_img.save(buffer, format=fmt)

            # Construct a simplified name for the image
            filename = f"part_{part.pk}_image.{fmt.lower()}"

            part.attachment.save(filename, ContentFile(buffer.getvalue()))

            return True

        return False

    def import_supplier_part(self, supplier_id, subito_list_id: str, part_id: str):
        print("Importing supplier part  " + subito_list_id)

        url = "v1/search/items?list_ids=" + str(subito_list_id)

        response = self.api_call(endpoint=url)

        supplier_part_expired = False

        if len(response['ads']) > 0:
            supplier_part_data = response['ads'][0]
        else: 
            supplier_part_expired = True

        part = Part.objects.get(pk=part_id)

        company = Company.objects.get(pk=supplier_id)

        supplier_part = SupplierPart.objects.get_or_create(
            part=part,
            company=company,
            SKU=subito_list_id,
        )[0]

        supplier_part.description = supplier_part_data['subject']
        supplier_part.note = supplier_part_data['body']
        supplier_part.link = supplier_part_data['urls']['default']
        supplier_part.available = 1

        # Save the whole object for good measure
        supplier_part.metadata['subito'] = supplier_part_data
        supplier_part.save()

        # Add images as part attachments
        for image in supplier_part_data['images']:

            image_url = image['scale'][4]['uri']

            part_attachment = PartAttachment.objects.get_or_create(
                part=part,
                comment=image['uri'] 
            )[0]

            self.import_image(image_url, part_attachment)

        # Add price break

        price = "0.0"
        for feature in supplier_part_data['features']:
            if feature['uri'] == '/price':
                price = feature['values'][0]['key']

        if(price != "0.0") :

            supplier_part_price = SupplierPriceBreak.objects.get_or_create(
                part=supplier_part,
                quantity=1,
            )[0]

            supplier_part_price.price = 0.0
            supplier_part_price.save()


    @property
    def api_url(self):
        """Base url path."""
        return "https://hades.subito.it/"

    def perform_action(self, user=None, data=None):

        command = data.get("command")

        if command == "add-supplier-part":

            supplier_id = 5

            part_id = data.get("part_id")
            subito_list_id = data.get("subito_list_id")

            self.import_supplier_part(supplier_id, part_id, subito_list_id)

    def get_info(self, user, data=None):
        """Sample method."""
        return {"user": user.username, "hello": "world"}

    def get_result(self, user=None, data=None):
        """Sample method."""
        return self.result
