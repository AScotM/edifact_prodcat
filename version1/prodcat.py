import re
from datetime import datetime
from typing import List, Dict, Union, Optional

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
            'partner_qualifier': 'ZZZ',  # Added partner qualifier configuration
            'test_indicator': None,      # Can be '1' for test messages
            'price_qualifier': 'AAA',    # Default price qualifier
            'quantity_qualifier': '12',  # Default quantity qualifier
            'description_type': 'F'     # Default description type (Free form)
        },
        'validation': {
            'allowed_id_chars': r'^[A-Z0-9\-\. ]{1,35}$',
            'required_product_fields': ['description', 'quantity', 'unit', 'price', 'currency'],
            'date_formats': {
                '102': r'^\d{8}$',       # YYYYMMDD
                '203': r'^\d{6}$',       # HHMMSS
                '102:203': r'^\d{8}:\d{6}$'  # YYYYMMDD:HHMMSS
            }
        }
    }

    def __init__(self, sender_id: str, receiver_id: str, message_ref: str = None):
        """
        Initialize with sender and receiver IDs.
        message_ref: Unique message reference number for UNH segment.
        """
        self.sender = self.validate_id(sender_id, "Sender ID")
        self.receiver = self.validate_id(receiver_id, "Receiver ID")
        self.message_ref = message_ref if message_ref else self._generate_message_reference()
        self.errors: List[str] = []
        self.interchange_control_reference = self._generate_interchange_reference()
        self.segment_count = 0  # Track segments for UNT

    def _generate_message_reference(self) -> str:
        """Generates a unique message reference based on timestamp."""
        return datetime.now().strftime("%Y%m%d%H%M%S%f")[:14]

    def _generate_interchange_reference(self) -> str:
        """Generates a unique interchange control reference."""
        return datetime.now().strftime("%H%M%S%f")[:6]

    def validate_id(self, id: str, field_name: str) -> str:
        """Validate IDs with more practical rules while maintaining compliance."""
        id = str(id).strip().upper()
        if not re.match(self.CONFIG['validation']['allowed_id_chars'], id):
            raise ValueError(
                f"{field_name} '{id}' must be 1-35 chars: letters, numbers, hyphens, periods or spaces"
            )
        return id

    def validate_date_format(self, date_value: str, format_code: str) -> bool:
        """Validate date values against expected formats."""
        pattern = self.CONFIG['validation']['date_formats'].get(format_code)
        if not pattern:
            return True  # No validation for unknown format codes
        return re.match(pattern, date_value) is not None

    def validate_product(self, product: Dict) -> bool:
        """Validate required product fields and data types."""
        missing_fields = [
            field for field in self.CONFIG['validation']['required_product_fields']
            if field not in product or not product[field]
        ]
        
        if missing_fields:
            self.errors.append(
                f"Product missing required fields: {', '.join(missing_fields)}"
            )
            return False
        
        # Validate price is numeric
        try:
            float(str(product['price']))
        except ValueError:
            self.errors.append(f"Invalid price value: {product['price']}")
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

        # Remove trailing empty data elements
        while len(segment_parts) > 1 and segment_parts[-1] == "":
            segment_parts.pop()

        self.segment_count += 1
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
                None,  # UNB6: Recipient's reference/password
                None,  # UNB7: Application reference
                None,  # UNB8: Processing priority code
                None,  # UNB9: Acknowledgement request
                None,  # UNB10: Communications agreement ID
                self.CONFIG['default_values']['test_indicator']  # UNB11: Test indicator
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
                None,  # UNH3: Common access reference
                None,  # UNH4: Status of the transfer
                None,  # UNH5: Message sub-type identifier
            ]
        )

    def _build_bgm_segment(self, document_number: str) -> str:
        """Builds the Beginning of Message (BGM) segment."""
        return self._format_segment(
            "BGM",
            [
                "220",  # BGM1: Document/message name, coded (220 for Product Catalogue)
                document_number,
                "9"  # BGM3: Message function, coded (9 for Original)
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
            party_qualifier,
            [party_id, None, None, None, "9"]  # NAD2: Party identification
        ]
        if name:
            elements.append(name)  # NAD3: Party name
        return self._format_segment("NAD", elements)

    def _build_lin_segment(self, line_item_number: int) -> str:
        """Builds a Line Item (LIN) segment."""
        return self._format_segment(
            "LIN",
            [
                str(line_item_number),  # LIN1: Line item number
                "1",  # LIN2: Action request/notification (1 for add)
                "EN"  # LIN3: Item number identification (EN for EAN/GTIN)
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
                [product_id, "BP"]  # PIA2: Product ID, Type
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
                None,  # IMD2: Item description coded
                None,  # IMD3: Item characteristic coded
                None,  # IMD4: Item description format
                description  # IMD5: Item description
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
                [price, "CT", currency]  # PRI2: Price, Type, Currency
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
                [quantity_qualifier, quantity, unit]
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
                [reference_qualifier, reference_number]
            ]
        )

    def _build_unt_segment(self) -> str:
        """Builds the Message Trailer (UNT) segment."""
        return self._format_segment(
            "UNT",
            [
                str(self.segment_count + 1),  # Includes UNT segment itself
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
        receiver_name: str = None
    ) -> bool:
        """
        Generates a PRODCAT EDIFACT file from a list of product dictionaries.
        
        Args:
            products: List of product dictionaries with required fields
            filename: Output filename
            document_number: Reference number for the catalogue
            sender_name: Optional sender name for NAD segment
            receiver_name: Optional receiver name for NAD segment
            
        Returns:
            bool: True if file was created successfully, False otherwise
        """
        self.errors = []  # Reset errors for new generation
        self.segment_count = 0  # Reset segment counter

        if not products:
            self.errors.append("No products provided for PRODCAT generation.")
            return False

        # Validate all products before processing
        for i, product in enumerate(products):
            if not self.validate_product(product):
                self.errors.append(f"Validation failed for product {i + 1}")
                return False

        if not document_number:
            document_number = datetime.now().strftime("PRODCAT-%Y%m%d%H%M%S")

        try:
            with open(filename, 'w') as f:
                # Write interchange header
                f.write(self._build_unb_segment() + '\n')
                
                # Write message header
                f.write(self._build_unh_segment() + '\n')
                
                # Beginning of message
                f.write(self._build_bgm_segment(document_number) + '\n')
                
                # Document date/time
                dtm_segment = self._build_dtm_segment(
                    "137", 
                    datetime.now().strftime("%Y%m%d"), 
                    "102"
                )
                if dtm_segment:
                    f.write(dtm_segment + '\n')
                
                # Parties
                f.write(self._build_nad_segment(
                    "BY", 
                    self.receiver, 
                    receiver_name or "Buyer Company Name"
                ) + '\n')
                
                f.write(self._build_nad_segment(
                    "SU", 
                    self.sender, 
                    sender_name or "Supplier Company Name"
                ) + '\n')
                
                # Product line items
                for i, product in enumerate(products):
                    line_item_number = i + 1

                    # LIN Segment
                    f.write(self._build_lin_segment(line_item_number) + '\n')

                    # PIA Segments
                    if product.get('supplier_code'):
                        f.write(self._build_pia_segment(
                            product['supplier_code'], 
                            "SA"
                        ) + '\n')

                    if product.get('barcode'):
                        f.write(self._build_pia_segment(
                            product['barcode'], 
                            "GT"
                        ) + '\n')

                    # IMD Segment
                    f.write(self._build_imd_segment(product['description']) + '\n')

                    # PRI Segment
                    f.write(self._build_pri_segment(
                        str(product['price']), 
                        product['currency']
                    ) + '\n')

                    # QTY Segment
                    f.write(self._build_qty_segment(
                        str(product['quantity']), 
                        product['unit']
                    ) + '\n')

                    # RFF Segment if provided
                    if product.get('internal_ref'):
                        f.write(self._build_rff_segment(
                            "AAN", 
                            product['internal_ref']
                        ) + '\n')

                # Message trailer
                f.write(self._build_unt_segment() + '\n')
                
                # Interchange trailer
                f.write(self._build_unz_segment() + '\n')
                
            return True
            
        except IOError as e:
            self.errors.append(f"Error writing EDI file: {e}")
            return False
        except Exception as e:
            self.errors.append(f"Unexpected error: {str(e)}")
            return False

if __name__ == "__main__":
    print("=== EDIFACT PRODCAT Generator ===")

    sample_products = [
        {
            'supplier_code': 'LAPTOP-001',
            'barcode': '1234567890123',
            'description': 'Premium Laptop 15" (16GB RAM/1TB SSD)',
            'quantity': '50',
            'unit': 'PCE',
            'price': '1299.99',
            'currency': 'USD',
            'internal_ref': 'INT-PROD-L001'
        },
        {
            'supplier_code': 'MONITOR-002',
            'barcode': '9876543210987',
            'description': '27" 4K UHD Monitor',
            'quantity': '100',
            'unit': 'PCE',
            'price': '499.00',
            'currency': 'EUR',
            'internal_ref': 'INT-PROD-M002'
        }
    ]

    try:
        generator = EDIFACTGenerator(
            sender_id="SENDER.GLN.123456789",
            receiver_id="RECEIVER-ABC-789"
        )

        success = generator.create_prodcat_file(
            products=sample_products,
            filename="prodcat_catalogue.edi",
            document_number="CATALOGUE-2024-001",
            sender_name="Tech Supplier Inc.",
            receiver_name="Electronics Retailer Ltd."
        )

        if success:
            print("\n✓ EDI PRODCAT file created successfully!")
            print("File: prodcat_catalogue.edi")
        else:
            print("\n✗ Failed to create EDI PRODCAT file.")
            for error_msg in generator.errors:
                print(f"  - {error_msg}")

    except ValueError as e:
        print(f"\n✗ Configuration Error: {e}")
    except Exception as e:
        print(f"\n✗ An unexpected error occurred: {e}")
