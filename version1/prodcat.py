import re
from datetime import datetime
from typing import List, Dict, Union

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
            'syntax_identifier': 'UNOA', # Changed from syntax_version
            'syntax_version_number': '3', # Changed from syntax_version
            'message_type': 'PRODCAT',    # Changed to PRODCAT
            'message_version': 'D',       # Version D
            'message_release': '01B',     # Release 01B
            'controlling_agency': 'UN',   # United Nations
            'association_assigned_code': 'UN' # For UNH 0052
        },
        'allowed_id_chars': r'^[A-Z0-9\-\. ]{1,35}$' # Allows hyphens, periods, and spaces
    }

    def __init__(self, sender_id: str, receiver_id: str, message_ref: str = None):
        """
        Initialize with sender and receiver IDs.
        message_ref: Unique message reference number for UNH segment.
        """
        self.sender = self.validate_id(sender_id, "Sender ID")
        self.receiver = self.validate_id(receiver_id, "Receiver ID")
        self.message_ref = message_ref if message_ref else self._generate_message_reference()
        self.errors = []
        self.interchange_control_reference = self._generate_interchange_reference()

    def _generate_message_reference(self) -> str:
        """Generates a unique message reference based on timestamp."""
        return datetime.now().strftime("%Y%m%d%H%M%S%f")[:14] # Up to 14 chars

    def _generate_interchange_reference(self) -> str:
        """Generates a unique interchange control reference."""
        return datetime.now().strftime("%H%M%S%f")[:6] # 6 chars

    def validate_id(self, id: str, field_name: str) -> str:
        """Validate IDs with more practical rules while maintaining compliance."""
        id = str(id).strip().upper()
        if not re.match(self.CONFIG['allowed_id_chars'], id):
            raise ValueError(
                f"{field_name} '{id}' must be 1-35 chars: letters, numbers, hyphens, periods or spaces"
            )
        return id

    def _format_segment(self, tag: str, elements: List[Union[str, List[str]]]) -> str:
        """Formats a single EDIFACT segment."""
        segment_parts = [tag]
        for element in elements:
            if isinstance(element, list):
                # Component data elements
                component_parts = [self._escape_data(c) for c in element if c is not None]
                segment_parts.append(self.CONFIG['delimiters']['component'].join(component_parts))
            elif element is not None:
                # Simple data element
                segment_parts.append(self._escape_data(str(element)))
            else:
                # Empty element, still append to maintain position
                segment_parts.append("")

        # Remove trailing empty data elements if they are at the very end
        while segment_parts and segment_parts[-1] == "":
            segment_parts.pop()

        return self.CONFIG['delimiters']['data'].join(segment_parts) + self.CONFIG['delimiters']['terminator']

    def _escape_data(self, data: str) -> str:
        """Escapes EDIFACT delimiters within data elements."""
        for char in self.CONFIG['delimiters'].values():
            if char in data:
                data = data.replace(char, self.CONFIG['delimiters']['escape'] + char)
        return data

    def _build_unb_segment(self) -> str:
        """Builds the Interchange Header (UNB) segment."""
        now = datetime.now()
        datetime_of_preparation = now.strftime("%y%m%d") + ":" + now.strftime("%H%M") # YYMMDD:HHMM

        return self._format_segment(
            "UNB",
            [
                [self.CONFIG['default_values']['syntax_identifier'], self.CONFIG['default_values']['syntax_version_number']], # UNB1: Syntax identifier, Syntax version number
                [self.sender, "ZZZ"], # UNB2: Sender identification, Partner identification code qualifier
                [self.receiver, "ZZZ"], # UNB3: Recipient identification, Partner identification code qualifier
                datetime_of_preparation, # UNB4: Date/time of preparation
                self.interchange_control_reference, # UNB5: Interchange control reference
                None, # UNB6: Recipient's reference/password
                None, # UNB7: Application reference
                None, # UNB8: Processing priority code
                None, # UNB9: Acknowledgement request
                None, # UNB10: Communications agreement ID
                None  # UNB11: Test indicator
            ]
        )

    def _build_unh_segment(self) -> str:
        """Builds the Message Header (UNH) segment."""
        return self._format_segment(
            "UNH",
            [
                self.message_ref, # UNH1: Message reference number
                [
                    self.CONFIG['default_values']['message_type'], # UNH2.1: Message type
                    self.CONFIG['default_values']['message_version'], # UNH2.2: Message version number
                    self.CONFIG['default_values']['message_release'], # UNH2.3: Message release number
                    self.CONFIG['default_values']['controlling_agency'], # UNH2.4: Controlling agency
                    self.CONFIG['default_values']['association_assigned_code'] # UNH2.5: Association assigned code
                ], # UNH2: Message identifier
                None, # UNH3: Common access reference
                None, # UNH4: Status of the transfer
                None, # UNH5: Message sub-type identifier
            ]
        )

    def _build_bgm_segment(self, document_number: str) -> str:
        """Builds the Beginning of Message (BGM) segment."""
        return self._format_segment(
            "BGM",
            [
                "220", # BGM1: Document/message name, coded (220 for Product Catalogue)
                document_number, # BGM2: Document/message number
                "9" # BGM3: Message function, coded (9 for Original)
            ]
        )

    def _build_dtm_segment(self, date_type_code: str, date_value: str, date_format: str) -> str:
        """Builds a Date/Time/Period (DTM) segment."""
        return self._format_segment(
            "DTM",
            [
                [date_type_code, date_value, date_format] # DTM1: Date/time/period
            ]
        )

    def _build_nad_segment(self, party_qualifier: str, party_id: str, name: str = None) -> str:
        """Builds a Name and Address (NAD) segment."""
        elements = [party_qualifier, [party_id, None, None, None, "9"]] # NAD1: Party qualifier, NAD2: Party identification (ID, code qualifier 9 for GLN/EAN)
        if name:
            elements.append(name) # NAD3: Party name
        return self._format_segment("NAD", elements)

    def _build_lin_segment(self, line_item_number: int) -> str:
        """Builds a Line Item (LIN) segment."""
        return self._format_segment(
            "LIN",
            [
                str(line_item_number), # LIN1: Line item number
                "1", # LIN2: Action request/notification, coded (1 for add)
                "EN" # LIN3: Item number identification (EN for EAN/GTIN)
            ]
        )

    def _build_pia_segment(self, product_id: str, qualifier: str = "BP") -> str:
        """
        Builds a Additional Product ID (PIA) segment.
        Qualifier: BP (Buyer's part number), SA (Supplier's article number),
                   GT (Global Trade Item Number - for GTIN/EAN)
        """
        return self._format_segment(
            "PIA",
            [
                qualifier, # PIA1: Product ID qualifier
                [product_id, "BP"] # PIA2: Product ID, Type (BP for Buyer's Part Number)
            ]
        )

    def _build_imd_segment(self, description: str, description_type: str = "F") -> str:
        """
        Builds an Item Description (IMD) segment.
        Description Type: F (Free form), C (Coded)
        """
        return self._format_segment(
            "IMD",
            [
                description_type, # IMD1: Item description type
                None, # IMD2: Item description coded
                None, # IMD3: Item characteristic coded
                None, # IMD4: Item description format
                description # IMD5: Item description
            ]
        )

    def _build_pri_segment(self, price: str, currency: str, price_qualifier: str = "AAA") -> str:
        """
        Builds a Price Details (PRI) segment.
        Price Qualifier: AAA (Catalogue price)
        """
        return self._format_segment(
            "PRI",
            [
                price_qualifier, # PRI1: Price qualifier
                [price, "CT", currency] # PRI2: Price, Type (CT for Catalogue), Currency
            ]
        )

    def _build_qty_segment(self, quantity: str, unit: str, quantity_qualifier: str = "12") -> str:
        """
        Builds a Quantity (QTY) segment.
        Quantity Qualifier: 12 (Base quantity)
        """
        return self._format_segment(
            "QTY",
            [
                [quantity_qualifier, quantity, unit] # QTY1: Quantity, Type, Unit
            ]
        )

    def _build_rff_segment(self, reference_qualifier: str, reference_number: str) -> str:
        """Builds a Reference (RFF) segment."""
        return self._format_segment(
            "RFF",
            [
                [reference_qualifier, reference_number] # RFF1: Reference
            ]
        )

    def _build_unt_segment(self, message_segment_count: int) -> str:
        """Builds the Message Trailer (UNT) segment."""
        return self._format_segment(
            "UNT",
            [
                str(message_segment_count), # UNT1: Number of segments in the message
                self.message_ref # UNT2: Message reference number
            ]
        )

    def _build_unz_segment(self, message_count: int) -> str:
        """Builds the Interchange Trailer (UNZ) segment."""
        return self._format_segment(
            "UNZ",
            [
                str(message_count), # UNZ1: Number of messages in the interchange
                self.interchange_control_reference # UNZ2: Interchange control reference
            ]
        )

    def create_prodcat_file(self, products: List[Dict], filename: str = "prodcat.edi",
                            document_number: str = None) -> bool:
        """
        Generates a PRODCAT EDIFACT file from a list of product dictionaries.
        Each product dictionary should contain:
        'supplier_code', 'barcode', 'description', 'quantity', 'unit', 'price', 'currency'
        """
        if not products:
            self.errors.append("No products provided for PRODCAT generation.")
            return False

        if not document_number:
            document_number = datetime.now().strftime("PRODCAT-%Y%m%d%H%M%S")

        edi_lines = []
        segment_count = 0 # To track segments within the message

        # Interchange Header
        edi_lines.append(self._build_unb_segment())
        segment_count += 1

        # Message Header
        edi_lines.append(self._build_unh_segment())
        segment_count += 1

        # Beginning of Message
        edi_lines.append(self._build_bgm_segment(document_number))
        segment_count += 1

        # Date/Time of Document
        edi_lines.append(self._build_dtm_segment("137", datetime.now().strftime("%Y%m%d"), "102")) # 137: Document/message date/time, 102: YYYYMMDD
        segment_count += 1

        # Sender and Receiver Parties
        edi_lines.append(self._build_nad_segment("BY", self.receiver, "Buyer Company Name")) # BY: Buyer
        segment_count += 1
        edi_lines.append(self._build_nad_segment("SU", self.sender, "Supplier Company Name")) # SU: Supplier
        segment_count += 1

        # Product Line Items
        for i, product in enumerate(products):
            line_item_number = i + 1

            # LIN Segment (Line Item)
            edi_lines.append(self._build_lin_segment(line_item_number))
            segment_count += 1

            # PIA Segment (Additional Product ID - Supplier Code)
            if 'supplier_code' in product and product['supplier_code']:
                edi_lines.append(self._build_pia_segment(product['supplier_code'], "SA")) # SA: Supplier's article number
                segment_count += 1

            # PIA Segment (Additional Product ID - Barcode/GTIN)
            if 'barcode' in product and product['barcode']:
                edi_lines.append(self._build_pia_segment(product['barcode'], "GT")) # GT: Global Trade Item Number
                segment_count += 1

            # IMD Segment (Item Description)
            if 'description' in product and product['description']:
                edi_lines.append(self._build_imd_segment(product['description'], "F")) # F: Free form description
                segment_count += 1

            # PRI Segment (Price Details)
            if 'price' in product and 'currency' in product and product['price'] and product['currency']:
                edi_lines.append(self._build_pri_segment(str(product['price']), product['currency'], "AAA")) # AAA: Catalogue price
                segment_count += 1

            # QTY Segment (Quantity - e.g., base quantity for catalogue)
            if 'quantity' in product and 'unit' in product and product['quantity'] and product['unit']:
                edi_lines.append(self._build_qty_segment(str(product['quantity']), product['unit'], "12")) # 12: Base quantity
                segment_count += 1

            # RFF Segment (Reference - e.g., internal product reference)
            if 'internal_ref' in product and product['internal_ref']:
                edi_lines.append(self._build_rff_segment("AAN", product['internal_ref'])) # AAN: Article number
                segment_count += 1

        # Message Trailer
        edi_lines.append(self._build_unt_segment(segment_count))
        segment_count += 1 # UNT is part of the message segment count

        # Interchange Trailer (always 1 message in this simple case)
        edi_lines.append(self._build_unz_segment(1))
        segment_count += 1 # UNZ is part of the interchange segment count (though not typically counted in UNT)

        try:
            with open(filename, 'w') as f:
                for line in edi_lines:
                    f.write(line + '\n')
            return True
        except IOError as e:
            self.errors.append(f"Error writing EDI file: {e}")
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
            document_number="CATALOGUE-2024-001"
        )

        if success:
            print("\n✓ EDI PRODCAT file created successfully!")
            print("File: prodcat_catalogue.edi")
            print("You can open this file with a text editor to view the EDIFACT message.")
        else:
            print("\n✗ Failed to create EDI PRODCAT file.")
            for error_msg in generator.errors:
                print(f"  - {error_msg}")

    except ValueError as e:
        print(f"\n✗ Configuration Error: {e}")
        print("Please check your sender/receiver IDs and try again.")
    except Exception as e:
        print(f"\n✗ An unexpected error occurred: {e}")

