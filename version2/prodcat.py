#!/usr/bin/env python3
import re
from datetime import datetime
from typing import List, Dict, Union, Optional, Any
import io
import os
import logging
import argparse
import json

class EDIFACTGenerator:
    """
    User-friendly EDIFACT PRODCAT generator that creates valid .edi files.
    This version is adapted to generate a simplified PRODCAT (Product Catalogue) message.
    """

    CONFIG = {
        'delimiters': {
            'component': ':',
            'data': '+',
            'decimal': '.',
            'escape': '?',
            'terminator': "'"
        },
        'default_values': {
            'syntax_identifier': 'UNOA',
            'syntax_version_number': '3',
            'message_type': 'PRODCAT',
            'message_version': 'D',
            'message_release': '01B',
            'controlling_agency': 'UN',
            'association_assigned_code': 'UN',
            'partner_qualifier': 'ZZZ',
            'test_indicator': None,
            'price_qualifier': 'AAA',
            'quantity_qualifier': '12',
            'description_type': 'F'
        },
        'validation': {
            'allowed_id_chars': r'^[A-Z0-9\-\. ]{1,35}$',
            'required_product_fields': ['description', 'quantity', 'unit', 'price', 'currency'],
            'valid_currencies': {'USD', 'EUR', 'GBP'},
            'valid_units': {'PCE', 'KGM', 'MTR'},
            'date_formats': {
                '102': r'^\d{8}$',
                '203': r'^\d{6}$',
                '102:203': r'^\d{8}:\d{6}$'
            }
        }
    }

    def __init__(self, sender_id: str, receiver_id: str, message_ref: str = None, logger: Optional[logging.Logger] = None):
        """Initialize with sender and receiver IDs."""
        self.logger = logger or logging.getLogger(__name__)
        self.sender = self.validate_id(sender_id, "Sender ID")
        self.receiver = self.validate_id(receiver_id, "Receiver ID")
        self.message_ref = message_ref if message_ref else self._generate_message_reference()
        self.errors: List[str] = []
        self.interchange_control_reference = self._generate_interchange_reference()
        self.segment_count = 0
        self.logger.info("Initialized EDIFACTGenerator with sender=%s, receiver=%s", sender_id, receiver_id)

    def _generate_message_reference(self) -> str:
        """Generates a unique message reference based on timestamp."""
        return datetime.now().strftime("%Y%m%d%H%M%S%f")[:14]

    def _generate_interchange_reference(self) -> str:
        """Generates a unique interchange control reference."""
        return datetime.now().strftime("%H%M%S%f")[:6]

    @staticmethod
    def sanitize_value(value: Any, uppercase: bool = False) -> str:
        """Sanitize input value by stripping and optionally converting to uppercase."""
        result = str(value).strip()
        return result.upper() if uppercase else result

    def validate_id(self, id: str, field_name: str) -> str:
        """Validate IDs with more practical rules while maintaining compliance."""
        id = self.sanitize_value(id, uppercase=True)
        if not re.match(self.CONFIG['validation']['allowed_id_chars'], id):
            raise ValueError(
                f"{field_name} '{id}' must be 1-35 chars: letters, numbers, hyphens, periods or spaces"
            )
        return id

    def validate_date_format(self, date_value: str, format_code: str) -> bool:
        """Validate date values against expected formats."""
        pattern = self.CONFIG['validation']['date_formats'].get(format_code)
        if not pattern:
            self.logger.warning("Unknown date format code: %s", format_code)
            return True
        return re.match(pattern, date_value) is not None

    def validate_product(self, product: Dict) -> bool:
        """Validate required product fields and data types."""
        missing_fields = [
            field for field in self.CONFIG['validation']['required_product_fields']
            if field not in product or not product[field]
        ]
        
        if missing_fields:
            self.errors.append(f"Product missing required fields: {', '.join(missing_fields)}")
            return False
        
        try:
            price = float(self.sanitize_value(product['price']))
            if price <= 0:
                self.errors.append(f"Price must be positive: {product['price']}")
                return False
        except ValueError:
            self.errors.append(f"Invalid price value: {product['price']}")
            return False
        
        try:
            quantity = float(self.sanitize_value(product['quantity']))
            if quantity <= 0:
                self.errors.append(f"Quantity must be positive: {product['quantity']}")
                return False
        except ValueError:
            self.errors.append(f"Invalid quantity value: {product['quantity']}")
            return False
        
        currency = self.sanitize_value(product['currency'], uppercase=True)
        if currency not in self.CONFIG['validation']['valid_currencies']:
            self.errors.append(f"Invalid currency: {currency}")
            return False
        
        unit = self.sanitize_value(product['unit'], uppercase=True)
        if unit not in self.CONFIG['validation']['valid_units']:
            self.errors.append(f"Invalid unit: {unit}")
            return False
        
        description = self.sanitize_value(product['description'])
        if self.CONFIG['default_values']['syntax_identifier'] == 'UNOA' and not description.isascii():
            self.errors.append(f"Description contains non-ASCII characters: {description}")
            return False
        
        for field in ['supplier_code', 'barcode', 'internal_ref']:
            if product.get(field) and not re.match(self.CONFIG['validation']['allowed_id_chars'], product[field]):
                self.errors.append(f"Invalid {field}: {product[field]}")
                return False
        
        return True

    def _format_segment(self, tag: str, elements: List[Union[str, List[str]]]) -> str:
        """Formats a single EDIFACT segment."""
        segment_parts = [tag]
        for element in elements:
            if isinstance(element, list):
                component_parts = [self._escape_data(c) for c in element if c is not None]
                segment_parts.append(
                    self.CONFIG['delimiters']['component'].join(component_parts)
                )
            elif element is not None:
                segment_parts.append(self._escape_data(str(element)))
            else:
                segment_parts.append("")

        while len(segment_parts) > 1 and segment_parts[-1] == "":
            segment_parts.pop()

        self.segment_count += 1
        self.logger.debug("Generated segment: %s", tag)
        return (
            self.CONFIG['delimiters']['data'].join(segment_parts) + 
            self.CONFIG['delimiters']['terminator']
        )

    def _escape_data(self, data: str) -> str:
        """Escapes EDIFACT delimiters within data elements."""
        for char in self.CONFIG['delimiters'].values():
            if char and char in data:
                data = data.replace(char, self.CONFIG['delimiters']['escape'] + char)
        return data

    def _build_unb_segment(self) -> str:
        """Builds the Interchange Header (UNB) segment."""
        now = datetime.now()
        datetime_of_preparation = now.strftime("%y%m%d") + ":" + now.strftime("%H%M")
        return self._format_segment(
            "UNB",
            [
                [
                    self.CONFIG['default_values']['syntax_identifier'],
                    self.CONFIG['default_values']['syntax_version_number']
                ],
                [self.sender, self.CONFIG['default_values']['partner_qualifier']],
                [self.receiver, self.CONFIG['default_values']['partner_qualifier']],
                datetime_of_preparation,
                self.interchange_control_reference,
                None, None, None, None, None,
                self.CONFIG['default_values']['test_indicator']
            ]
        )

    def _build_unh_segment(self) -> str:
        """Builds the Message Header (UNH) segment."""
        return self._format_segment(
            "UNH",
            [
                self.message_ref,
                [
                    self.CONFIG['default_values']['message_type'],
                    self.CONFIG['default_values']['message_version'],
                    self.CONFIG['default_values']['message_release'],
                    self.CONFIG['default_values']['controlling_agency'],
                    self.CONFIG['default_values']['association_assigned_code']
                ],
                None, None, None
            ]
        )

    def _build_bgm_segment(self, document_number: str) -> str:
        """Builds the Beginning of Message (BGM) segment."""
        return self._format_segment(
            "BGM",
            [
                "220",
                self.sanitize_value(document_number),
                "9"
            ]
        )

    def _build_dtm_segment(
        self, 
        date_type_code: str, 
        date_value: str, 
        date_format: str
    ) -> Optional[str]:
        """Builds a Date/Time/Period (DTM) segment with validation."""
        if not self.validate_date_format(date_value, date_format):
            self.errors.append(
                f"Invalid date format for {date_type_code}. "
                f"Value: {date_value}, Expected format: {date_format}"
            )
            return None
        return self._format_segment(
            "DTM",
            [[date_type_code, date_value, date_format]]
        )

    def _build_nad_segment(
        self, 
        party_qualifier: str, 
        party_id: str, 
        name: str = None
    ) -> str:
        """Builds a Name and Address (NAD) segment."""
        elements = [
            self.sanitize_value(party_qualifier, uppercase=True),
            [self.sanitize_value(party_id), None, None, None, "9"]
        ]
        if name:
            elements.append(self.sanitize_value(name))
        return self._format_segment("NAD", elements)

    def _build_lin_segment(self, line_item_number: int) -> str:
        """Builds a Line Item (LIN) segment."""
        return self._format_segment(
            "LIN",
            [
                str(line_item_number),
                "1",
                "EN"
            ]
        )

    def _build_pia_segment(
        self, 
        product_id: str, 
        qualifier: str = "BP"
    ) -> str:
        """Builds a Additional Product ID (PIA) segment."""
        return self._format_segment(
            "PIA",
            [
                qualifier,
                [self.sanitize_value(product_id), "BP"]
            ]
        )

    def _build_imd_segment(
        self, 
        description: str, 
        description_type: str = None
    ) -> str:
        """Builds an Item Description (IMD) segment."""
        if description_type is None:
            description_type = self.CONFIG['default_values']['description_type']
        return self._format_segment(
            "IMD",
            [
                description_type,
                None, None, None,
                self.sanitize_value(description)
            ]
        )

    def _build_pri_segment(
        self, 
        price: str, 
        currency: str, 
        price_qualifier: str = None
    ) -> str:
        """Builds a Price Details (PRI) segment."""
        if price_qualifier is None:
            price_qualifier = self.CONFIG['default_values']['price_qualifier']
        return self._format_segment(
            "PRI",
            [
                price_qualifier,
                [self.sanitize_value(price), "CT", self.sanitize_value(currency, uppercase=True)]
            ]
        )

    def _build_qty_segment(
        self, 
        quantity: str, 
        unit: str, 
        quantity_qualifier: str = None
    ) -> str:
        """Builds a Quantity (QTY) segment."""
        if quantity_qualifier is None:
            quantity_qualifier = self.CONFIG['default_values']['quantity_qualifier']
        return self._format_segment(
            "QTY",
            [
                [quantity_qualifier, self.sanitize_value(quantity), self.sanitize_value(unit, uppercase=True)]
            ]
        )

    def _build_rff_segment(
        self, 
        reference_qualifier: str, 
        reference_number: str
    ) -> str:
        """Builds a Reference (RFF) segment."""
        return self._format_segment(
            "RFF",
            [
                [reference_qualifier, self.sanitize_value(reference_number)]
            ]
        )

    def _build_unt_segment(self) -> str:
        """Builds the Message Trailer (UNT) segment."""
        return self._format_segment(
            "UNT",
            [
                str(self.segment_count + 1),
                self.message_ref
            ]
        )

    def _build_unz_segment(self, message_count: int = 1) -> str:
        """Builds the Interchange Trailer (UNZ) segment."""
        return self._format_segment(
            "UNZ",
            [
                str(message_count),
                self.interchange_control_reference
            ]
        )

    def create_prodcat_file(
        self, 
        products: List[Dict], 
        filename: str = "prodcat.edi",
        document_number: str = None,
        sender_name: str = None,
        receiver_name: str = None,
        force: bool = False
    ) -> bool:
        """
        Generates a PRODCAT EDIFACT file from a list of product dictionaries.
        
        Args:
            products: List of product dictionaries with required fields
            filename: Output filename
            document_number: Reference number for the catalogue
            sender_name: Optional sender name for NAD segment
            receiver_name: Optional receiver name for NAD segment
            force: Overwrite existing file if True
            
        Returns:
            bool: True if file was created successfully, False otherwise
        """
        self.errors = []
        self.segment_count = 0
        self.logger.info("Generating PRODCAT file: %s", filename)

        if not force and os.path.exists(filename):
            self.errors.append(f"File {filename} exists. Use --force to overwrite.")
            return False

        if not products:
            self.errors.append("No products provided for PRODCAT generation.")
            return False

        valid_products = [p for p in products if self.validate_product(p)]
        if not valid_products:
            self.errors.append("No valid products to process")
            return False

        if not document_number:
            document_number = datetime.now().strftime("PRODCAT-%Y%m%d%H%M%S")

        try:
            buffer = io.StringIO()
            buffer.write(self._build_unb_segment() + '\n')
            buffer.write(self._build_unh_segment() + '\n')
            buffer.write(self._build_bgm_segment(document_number) + '\n')
            
            dtm_segment = self._build_dtm_segment(
                "137", 
                datetime.now().strftime("%Y%m%d"), 
                "102"
            )
            if dtm_segment:
                buffer.write(dtm_segment + '\n')
            else:
                self.segment_count -= 1  # Adjust for skipped segment
                
            buffer.write(self._build_nad_segment(
                "BY", 
                self.receiver, 
                receiver_name or "Buyer Company Name"
            ) + '\n')
            buffer.write(self._build_nad_segment(
                "SU", 
                self.sender, 
                sender_name or "Supplier Company Name"
            ) + '\n')
            
            for i, product in enumerate(valid_products, 1):
                buffer.write(self._build_lin_segment(i) + '\n')
                if product.get('supplier_code'):
                    buffer.write(self._build_pia_segment(product['supplier_code'], "SA") + '\n')
                if product.get('barcode'):
                    buffer.write(self._build_pia_segment(product['barcode'], "GT") + '\n')
                buffer.write(self._build_imd_segment(product['description']) + '\n')
                buffer.write(self._build_pri_segment(
                    str(product['price']), 
                    product['currency']
                ) + '\n')
                buffer.write(self._build_qty_segment(
                    str(product['quantity']), 
                    product['unit']
                ) + '\n')
                if product.get('internal_ref'):
                    buffer.write(self._build_rff_segment("AAN", product['internal_ref']) + '\n')
            
            buffer.write(self._build_unt_segment() + '\n')
            buffer.write(self._build_unz_segment() + '\n')
            
            output = buffer.getvalue()
            buffer.close()
            
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(output)
            self.logger.info("Successfully wrote PRODCAT file: %s", filename)
            return True
            
        except IOError as e:
            self.errors.append(f"Error writing EDI file: {e}")
            self.logger.error("Failed to write EDI file: %s", e)
            return False
        except Exception as e:
            self.errors.append(f"Unexpected error: {str(e)}")
            self.logger.error("Unexpected error: %s", e)
            return False

def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure application logging with customizable level."""
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger

if __name__ == "__main__":
    logger = configure_logging()
    logger.info("Starting EDIFACT PRODCAT Generator")

    parser = argparse.ArgumentParser(description="Generate EDIFACT PRODCAT messages")
    parser.add_argument("--input", help="JSON file with product data")
    parser.add_argument("--output", default="prodcat.edi", help="Output EDI file")
    parser.add_argument("--sender", default="SENDER.GLN.123456789", help="Sender ID")
    parser.add_argument("--receiver", default="RECEIVER-ABC-789", help="Receiver ID")
    parser.add_argument("--force", action="store_true", help="Overwrite existing file")
    args = parser.parse_args()

    try:
        if args.input:
            with open(args.input, "r", encoding="utf-8") as f:
                products = json.load(f)
            logger.info("Loaded product data from %s", args.input)
        else:
            products = [
                {
                    'supplier_code': 'LAPTOP-001',
                    'barcode': '1234567890123',
                    'description': 'Premium Laptop 15 inch (16GB RAM/1TB SSD)',
                    'quantity': '50',
                    'unit': 'PCE',
                    'price': '1299.99',
                    'currency': 'USD',
                    'internal_ref': 'INT-PROD-L001'
                },
                {
                    'supplier_code': 'MONITOR-002',
                    'barcode': '9876543210987',
                    'description': '27 inch 4K UHD Monitor',
                    'quantity': '100',
                    'unit': 'PCE',
                    'price': '499.00',
                    'currency': 'EUR',
                    'internal_ref': 'INT-PROD-M002'
                }
            ]
            logger.info("Using sample product data")

        generator = EDIFACTGenerator(
            sender_id=args.sender,
            receiver_id=args.receiver,
            logger=logger
        )

        success = generator.create_prodcat_file(
            products=products,
            filename=args.output,
            document_number="CATALOGUE-2024-001",
            sender_name="Tech Supplier Inc.",
            receiver_name="Electronics Retailer Ltd.",
            force=args.force
        )

        if success:
            print("\n✓ EDI PRODCAT file created successfully!")
            print(f"File: {args.output}")
        else:
            print("\n✗ Failed to create EDI PRODCAT file.")
            for error_msg in generator.errors:
                print(f"  - {error_msg}")

    except (ValueError, json.JSONDecodeError, OSError) as e:
        logger.error("Error: %s", e)
        print(f"\n✗ Error: {e}")
