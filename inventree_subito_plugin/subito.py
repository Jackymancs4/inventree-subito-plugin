"""Sample implementation for ActionMixin."""

import io
import inspect, json, logging
from django.urls import path
from django.http import HttpResponse
from plugin import InvenTreePlugin
from plugin.mixins import (
    ActionMixin,
    APICallMixin,
    SettingsMixin,
    PanelMixin,
    UrlsMixin,
)
from company.models import Company, SupplierPriceBreak
from part.models import (
    Part,
    SupplierPart,
    PartAttachment,
)
from InvenTree.helpers_model import download_image_from_url
from django.core.files.base import ContentFile
from InvenTree.tasks import offload_task
from part.views import PartDetail
from common.notifications import trigger_notification, UIMessageNotification

logger = logging.getLogger("subitoplugin")


class SubitoPlugin(
    ActionMixin, APICallMixin, SettingsMixin, PanelMixin, UrlsMixin, InvenTreePlugin
):
    """An action plugin which offers variuous integrations with Subito.it."""

    NAME = "SubitoPlugin"
    SLUG = "subito"
    ACTION_NAME = "subito"
    TITLE = "Subito.it Integration"
    AUTHOR = "Jackymancs4"
    LICENSE = "MIT"

    result = {}

    SETTINGS = {
        "SUBITOIT_COMPANY_ID": {
            "name": "Subito.it Company",
            "description": "The Company which acts as a Supplier for all Subito.it Parts",
            "model": "company.company",
        },
    }

    def import_image(self, url: str, part: PartAttachment) -> bool:
        """
        Download an image given it's URL, and attach it to the part.
        Would be cool to attach it to the SupplierPart, but it's not really a
        thing for now.
        """

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

    def import_image_async(self, url, part):
        """
        Async version of the same method.
        """

        offload_task(self.import_image, url, part)

    def import_supplier_part(
        self, supplier_id, part_id: str, subito_list_id: str
    ) -> SupplierPart:
        """
        Add a supplier part
        """

        logger.info("Importing supplier part " + subito_list_id)

        url = "v1/search/items?list_ids=" + str(subito_list_id)

        response = self.api_call(endpoint=url)

        supplier_part_retired = False

        # If there are no ads, than the add has been retired.
        if len(response["ads"]) > 0:
            supplier_part_data = response["ads"][0]
        else:
            supplier_part_retired = True

        part = Part.objects.get(pk=part_id)

        company = Company.objects.get(pk=supplier_id)

        supplier_part = SupplierPart.objects.get_or_create(
            part=part,
            supplier=company,
            SKU=subito_list_id,
        )[0]

        if supplier_part_retired:
            supplier_part.active = False
            supplier_part.update_available_quantity(0)
            # supplier_part.save()
        else:
            supplier_part.active = True
            supplier_part.description = supplier_part_data["subject"]
            # TODO: change this to the full body
            supplier_part.note = (
                (supplier_part_data["body"][:98] + "..")
                if len(supplier_part_data["body"]) > 100
                else supplier_part_data["body"]
            )
            supplier_part.link = supplier_part_data["urls"]["default"]

            # Save the whole object for good measure
            supplier_part.metadata["subito"] = supplier_part_data
            supplier_part.update_available_quantity(1)
            # supplier_part.save()

            # Add images as part attachments
            for image in supplier_part_data["images"]:

                image_url = image["scale"][4]["uri"]

                part_attachment = PartAttachment.objects.get_or_create(
                    part=part, link=image_url, comment=image["uri"]
                )[0]

                self.import_image(image_url, part_attachment)

            # Add price break
            price = "0.0"
            for feature in supplier_part_data["features"]:
                if feature["uri"] == "/price":
                    price = feature["values"][0]["key"]

            if price != "0.0":

                supplier_part_price = SupplierPriceBreak.objects.get_or_create(
                    part=supplier_part,
                    quantity=1,
                )[0]

                supplier_part_price.price = price
                supplier_part_price.price_currency = "EUR"
                supplier_part_price.save()

        return supplier_part

    @property
    def api_url(self):
        """Base url path."""
        return "https://hades.subito.it/"

    def perform_action(self, user=None, data=None):

        command = data.get("command")

        supplier_id = self.get_setting("SUBITOIT_COMPANY_ID")

        if command == "add_supplier_part":

            part_id = data.get("part_id")
            subito_list_id = data.get("subito_list_id")

            self.import_supplier_part(supplier_id, part_id, subito_list_id)

        if command == "update_supplier_parts":

            supplier_parts = SupplierPart.objects.filter(metadata__icontains="subito")

            for supplier_part in supplier_parts:
                logger.debug("Supplier part found: " + str(supplier_part.pk))

                self.import_supplier_part(
                    supplier_id, supplier_part.part.pk, supplier_part.SKU
                )

    def get_info(self, user, data=None):
        """Sample method."""
        return {"user": user.username, "hello": "world"}

    def get_result(self, user=None, data=None):
        """Sample method."""
        return self.result

    def get_custom_panels(self, view, request):
        panels = []

        if isinstance(view, PartDetail):

            self.item = view.get_object()

            panels.append(
                {
                    "title": "Subito.it Action",
                    "icon": "fa-building ",
                    "content_template": "subito/subito.html",
                }
            )

        return panels

    def setup_urls(self):
        return [
            path(
                "add_supplier_part/<int:part_id>/<int:subito_list_id>/",
                self.add_supplier_part,
                name="add_supplier_part",
            ),
        ]

    # Define the function that will be called.
    def add_supplier_part(
        self,
        request,
        part_id,
        subito_list_id,
    ):

        supplier_id = self.get_setting("SUBITOIT_COMPANY_ID")

        supplier_part = self.import_supplier_part(
            supplier_id, str(part_id), str(subito_list_id)
        )

        users = [request.user]

        trigger_notification(
            supplier_part,
            "inventree.plugin",
            context={
                "error": None,
                "name": "Subito.it supplier part",
                "message": "Supplier part created",
            },
            targets=users,
            delivery_methods={UIMessageNotification},
        )

        return HttpResponse(f"OK")
