#!/usr/bin/env python3
import re
from datetime import datetime
from typing import List, Dict, Union, Optional, Any, Callable
import io
import os
import logging
import argparse
import json
import time
from pathlib import Path

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
            'valid_currencies': {'USD', 'EUR', 'GBP', 'JPY', 'CAD', 'AUD', 'CHF', 'CNY'},
            'valid_units': {'PCE', 'KGM', 'MTR', 'LTR', 'MTK', 'MTQ', 'CMT'},
            'date_formats': {
                '102': r'^\d{8}$',
                '203': r'^\d{6}$',
                '102:203': r'^\d{8}:\d{6}$'
            }
        },
        'limits': {
            'max_products_per_file': 10000,
            'chunk_size': 1000,
            'max_file_size_mb': 50
        }
    }

    def __init__(self, sender_id: str, receiver_id: str, message_ref: str = None, 
                 config_file: Optional[str] = None, logger: Optional[logging.Logger] = None):
        """Initialize with sender and receiver IDs."""
        self.logger = logger or logging.getLogger(__name__)
        self.sender = self.validate_id(sender_id, "Sender ID")
        self.receiver = self.validate_id(receiver_id, "Receiver ID")
        self.message_ref = message_ref if message_ref else self._generate_message_reference()
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.interchange_control_reference = self._generate_interchange_reference()
        self.segment_count = 0
        self._custom_validators: Dict[str, tuple] = {}
        self._segment_templates: Dict[str, List] = {}
        self._load_default_templates()
        
        if config_file:
            self._load_config(config_file)
            
        self.logger.info("Initialized EDIFACTGenerator with sender=%s, receiver=%s", sender_id, receiver_id)

    def _load_config(self, config_file: str):
        """Load configuration from JSON file."""
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                external_config = json.load(f)
                self._merge_configs(external_config)
            self.logger.info("Loaded configuration from %s", config_file)
        except Exception as e:
            self.logger.warning("Failed to load config file %s: %s", config_file, e)
            self.warnings.append(f"Config file load failed: {e}")

    def _merge_configs(self, external_config: Dict):
        """Deep merge external configuration with defaults."""
        for key, value in external_config.items():
            if key in self.CONFIG and isinstance(value, dict) and isinstance(self.CONFIG[key], dict):
                self.CONFIG[key].update(value)
            else:
                self.CONFIG[key] = value

    def _load_default_templates(self):
        """Load default segment templates."""
        self._segment_templates = {
            'product_header': ['LIN', '${line_number}', '1', 'EN'],
            'product_id': ['PIA', '${qualifier}', ['${product_id}', '${id_type}']],
            'product_description': ['IMD', '${desc_type}', None, None, None, '${description}'],
            'product_price': ['PRI', '${price_qualifier}', ['${price}', 'CT', '${currency}']],
            'product_quantity': ['QTY', ['${qty_qualifier}', '${quantity}', '${unit}']],
            'product_reference': ['RFF', ['${ref_qualifier}', '${reference}']]
        }

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

    def add_validation_rule(self, field: str, validator: Callable[[Any], bool], message: str):
        """Allow custom validation rules."""
        self._custom_validators[field] = (validator, message)
        self.logger.debug("Added custom validation rule for field: %s", field)

    def validate_product(self, product: Dict) -> bool:
        """Validate required product fields and data types with custom rules."""
        missing_fields = [
            field for field in self.CONFIG['validation']['required_product_fields']
            if field not in product or not product[field]
        ]
        
        if missing_fields:
            self.errors.append(f"Product missing required fields: {', '.join(missing_fields)}")
            return False
        
        # Price validation
        try:
            price = float(self.sanitize_value(product['price']))
            if price <= 0:
                self.errors.append(f"Price must be positive: {product['price']}")
                return False
        except ValueError:
            self.errors.append(f"Invalid price value: {product['price']}")
            return False
        
        # Quantity validation
        try:
            quantity = float(self.sanitize_value(product['quantity']))
            if quantity <= 0:
                self.errors.append(f"Quantity must be positive: {product['quantity']}")
                return False
        except ValueError:
            self.errors.append(f"Invalid quantity value: {product['quantity']}")
            return False
        
        # Currency validation
        currency = self.sanitize_value(product['currency'], uppercase=True)
        if currency not in self.CONFIG['validation']['valid_currencies']:
            self.errors.append(f"Invalid currency: {currency}")
            return False
        
        # Unit validation
        unit = self.sanitize_value(product['unit'], uppercase=True)
        if unit not in self.CONFIG['validation']['valid_units']:
            self.errors.append(f"Invalid unit: {unit}")
            return False
        
        # Description validation
        description = self.sanitize_value(product['description'])
        if self.CONFIG['default_values']['syntax_identifier'] == 'UNOA' and not description.isascii():
            self.errors.append(f"Description contains non-ASCII characters: {description}")
            return False
        
        # Additional field validation
        for field in ['supplier_code', 'barcode', 'internal_ref']:
            if product.get(field) and not re.match(self.CONFIG['validation']['allowed_id_chars'], product[field]):
                self.errors.append(f"Invalid {field}: {product[field]}")
                return False
        
        # Custom validators
        for field, (validator, message) in self._custom_validators.items():
            if field in product and not validator(product[field]):
                self.errors.append(f"{field}: {message}")
                return False
        
        return True

    def register_segment_template(self, segment_type: str, template: List):
        """Register custom segment templates."""
        self._segment_templates[segment_type] = template
        self.logger.debug("Registered segment template for: %s", segment_type)

    def _build_segment_from_template(self, segment_type: str, **kwargs) -> str:
        """Build segment using template."""
        template = self._segment_templates.get(segment_type)
        if not template:
            raise ValueError(f"Unknown segment type: {segment_type}")
        
        resolved_elements = []
        for element in template:
            if isinstance(element, str) and element.startswith('${'):
                key = element[2:-1]
                resolved_elements.append(kwargs.get(key, ""))
            elif isinstance(element, list):
                resolved_subelements = []
                for subelement in element:
                    if isinstance(subelement, str) and subelement.startswith('${'):
                        subkey = subelement[2:-1]
                        resolved_subelements.append(kwargs.get(subkey, ""))
                    else:
                        resolved_subelements.append(subelement)
                resolved_elements.append(resolved_subelements)
            else:
                resolved_elements.append(element)
        
        return self._format_segment(segment_type.upper(), resolved_elements)

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

    def _build_ftx_segment(self, text: str, subject_qualifier: str = "ADE") -> str:
        """Build Free Text (FTX) segment for additional product information."""
        return self._format_segment(
            "FTX",
            [
                subject_qualifier,
                None,
                None,
                None,
                [self.sanitize_value(text)]
            ]
        )

    def _build_meas_segment(self, dimension: str, value: float, unit: str) -> str:
        """Build Measurements (MEA) segment for product dimensions."""
        return self._format_segment(
            "MEA",
            [
                "PD",
                "AAD",
                [dimension, str(value), unit]
            ]
        )

    def set_edifact_version(self, version: str):
        """Configure generator for different EDIFACT versions."""
        version_configs = {
            'UNOA': {'syntax_identifier': 'UNOA', 'syntax_version': '3'},
            'UNOB': {'syntax_identifier': 'UNOB', 'syntax_version': '4'},
            'UNOC': {'syntax_identifier': 'UNOC', 'syntax_version': '1'},
        }
        
        if version not in version_configs:
            raise ValueError(f"Unsupported EDIFACT version: {version}")
        
        config = version_configs[version]
        self.CONFIG['default_values']['syntax_identifier'] = config['syntax_identifier']
        self.CONFIG['default_values']['syntax_version_number'] = config['syntax_version']
        self.logger.info("Set EDIFACT version to: %s", version)

    def create_prodcat_files(
        self, 
        products: List[Dict], 
        max_products_per_file: int = None,
        base_filename: str = "prodcat"
    ) -> List[str]:
        """Split large product catalogs into multiple files."""
        if max_products_per_file is None:
            max_products_per_file = self.CONFIG['limits']['max_products_per_file']
        
        created_files = []
        total_files = (len(products) + max_products_per_file - 1) // max_products_per_file
        
        self.logger.info("Splitting %d products into %d files", len(products), total_files)
        
        for i, chunk_start in enumerate(range(0, len(products), max_products_per_file)):
            chunk = products[chunk_start:chunk_start + max_products_per_file]
            filename = f"{base_filename}_{i+1:03d}.edi"
            
            self.logger.info("Generating file %d/%d: %s", i+1, total_files, filename)
            
            if self.create_prodcat_file(chunk, filename, force=True):
                created_files.append(filename)
            else:
                self.logger.error("Failed to generate file: %s", filename)
        
        return created_files

    def create_prodcat_file(
        self, 
        products: List[Dict], 
        filename: str = "prodcat.edi",
        document_number: str = None,
        sender_name: str = None,
        receiver_name: str = None,
        force: bool = False,
        use_streaming: bool = False
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
            use_streaming: Use streaming for large files
            
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

        # Check if we should use streaming for large files
        if use_streaming or len(products) > self.CONFIG['limits']['chunk_size']:
            return self.create_prodcat_file_streaming(
                products, filename, document_number, sender_name, receiver_name
            )

        valid_products = [p for p in products if self.validate_product(p)]
        if len(valid_products) != len(products):
            self.logger.warning("%d of %d products failed validation", 
                              len(products) - len(valid_products), len(products))
        
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
            
            # Verify the generated file
            verification = self.verify_edi_file(filename)
            if not verification['valid']:
                self.logger.warning("EDI file verification failed: %s", verification['errors'])
            
            return True
            
        except IOError as e:
            self.errors.append(f"Error writing EDI file: {e}")
            self.logger.error("Failed to write EDI file: %s", e)
            return False
        except Exception as e:
            self.errors.append(f"Unexpected error: {str(e)}")
            self.logger.error("Unexpected error: %s", e)
            return False

    def create_prodcat_file_streaming(
        self, 
        products: List[Dict], 
        filename: str,
        document_number: str = None,
        sender_name: str = None,
        receiver_name: str = None
    ) -> bool:
        """Generate EDI file using streaming to handle large datasets."""
        self.logger.info("Using streaming mode for %d products", len(products))
        
        if not document_number:
            document_number = datetime.now().strftime("PRODCAT-%Y%m%d%H%M%S")

        try:
            with open(filename, 'w', encoding='utf-8') as f:
                # Write headers
                f.write(self._build_unb_segment() + '\n')
                f.write(self._build_unh_segment() + '\n')
                f.write(self._build_bgm_segment(document_number) + '\n')
                
                dtm_segment = self._build_dtm_segment("137", datetime.now().strftime("%Y%m%d"), "102")
                if dtm_segment:
                    f.write(dtm_segment + '\n')
                else:
                    self.segment_count -= 1
                    
                f.write(self._build_nad_segment("BY", self.receiver, receiver_name or "Buyer Company Name") + '\n')
                f.write(self._build_nad_segment("SU", self.sender, sender_name or "Supplier Company Name") + '\n')

                # Write products in chunks
                valid_count = 0
                chunk_size = self.CONFIG['limits']['chunk_size']
                
                for i in range(0, len(products), chunk_size):
                    chunk = products[i:i + chunk_size]
                    for j, product in enumerate(chunk, i + 1):
                        if not self.validate_product(product):
                            continue
                            
                        valid_count += 1
                        f.write(self._build_lin_segment(valid_count) + '\n')
                        
                        if product.get('supplier_code'):
                            f.write(self._build_pia_segment(product['supplier_code'], "SA") + '\n')
                        if product.get('barcode'):
                            f.write(self._build_pia_segment(product['barcode'], "GT") + '\n')
                            
                        f.write(self._build_imd_segment(product['description']) + '\n')
                        f.write(self._build_pri_segment(str(product['price']), product['currency']) + '\n')
                        f.write(self._build_qty_segment(str(product['quantity']), product['unit']) + '\n')
                        
                        if product.get('internal_ref'):
                            f.write(self._build_rff_segment("AAN", product['internal_ref']) + '\n')
                    
                    # Flush periodically
                    if i % (chunk_size * 10) == 0:
                        f.flush()
                        self.logger.debug("Flushed buffer at product %d", i)

                # Write trailers
                f.write(self._build_unt_segment() + '\n')
                f.write(self._build_unz_segment() + '\n')

            self.logger.info("Streaming generation completed. %d valid products written", valid_count)
            return True
            
        except Exception as e:
            self.errors.append(f"Streaming generation failed: {e}")
            self.logger.error("Streaming generation failed: %s", e)
            return False

    def verify_edi_file(self, filename: str) -> Dict[str, Any]:
        """Verify generated EDI file structure and syntax."""
        verification_result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'statistics': {}
        }
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check segment sequence
            segments = [line.strip() for line in content.split('\n') if line.strip()]
            expected_start = ['UNB', 'UNH', 'BGM']
            expected_end = ['UNT', 'UNZ']
            
            for i, expected in enumerate(expected_start):
                if not segments[i].startswith(expected):
                    verification_result['errors'].append(f"Missing {expected} segment at position {i}")
                    verification_result['valid'] = False
            
            for i, expected in enumerate(expected_end, 1):
                if not segments[-i].startswith(expected):
                    verification_result['errors'].append(f"Missing {expected} segment at end")
                    verification_result['valid'] = False
            
            # Count segments
            verification_result['statistics'] = {
                'total_segments': len(segments),
                'product_lines': len([s for s in segments if s.startswith('LIN')]),
                'file_size_bytes': len(content),
                'file_size_mb': round(len(content) / (1024 * 1024), 2)
            }
            
            # Check file size limits
            max_size_mb = self.CONFIG['limits']['max_file_size_mb']
            if verification_result['statistics']['file_size_mb'] > max_size_mb:
                verification_result['warnings'].append(
                    f"File size ({verification_result['statistics']['file_size_mb']} MB) "
                    f"exceeds recommended limit ({max_size_mb} MB)"
                )
            
        except Exception as e:
            verification_result['valid'] = False
            verification_result['errors'].append(f"Verification failed: {e}")
        
        return verification_result

    # Template-based segment builders (convenience methods)
    def _build_lin_segment(self, line_item_number: int) -> str:
        return self._build_segment_from_template(
            'product_header',
            line_number=str(line_item_number)
        )

    def _build_pia_segment(self, product_id: str, qualifier: str = "BP") -> str:
        return self._build_segment_from_template(
            'product_id',
            product_id=product_id,
            qualifier=qualifier,
            id_type=qualifier
        )

    def _build_imd_segment(self, description: str, description_type: str = None) -> str:
        if description_type is None:
            description_type = self.CONFIG['default_values']['description_type']
        return self._build_segment_from_template(
            'product_description',
            description=description,
            desc_type=description_type
        )

    def _build_pri_segment(self, price: str, currency: str, price_qualifier: str = None) -> str:
        if price_qualifier is None:
            price_qualifier = self.CONFIG['default_values']['price_qualifier']
        return self._build_segment_from_template(
            'product_price',
            price=price,
            currency=currency,
            price_qualifier=price_qualifier
        )

    def _build_qty_segment(self, quantity: str, unit: str, quantity_qualifier: str = None) -> str:
        if quantity_qualifier is None:
            quantity_qualifier = self.CONFIG['default_values']['quantity_qualifier']
        return self._build_segment_from_template(
            'product_quantity',
            quantity=quantity,
            unit=unit,
            qty_qualifier=quantity_qualifier
        )

    def _build_rff_segment(self, reference_qualifier: str, reference_number: str) -> str:
        return self._build_segment_from_template(
            'product_reference',
            ref_qualifier=reference_qualifier,
            reference=reference_number
        )

    def _build_unt_segment(self) -> str:
        return self._format_segment(
            "UNT",
            [
                str(self.segment_count + 1),
                self.message_ref
            ]
        )

    def _build_unz_segment(self, message_count: int = 1) -> str:
        return self._format_segment(
            "UNZ",
            [
                str(message_count),
                self.interchange_control_reference
            ]
        )

def configure_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure application logging with customizable level."""
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(level)
    return logger

def main():
    """Main CLI entry point with enhanced options."""
    parser = argparse.ArgumentParser(description="Generate EDIFACT PRODCAT messages")
    parser.add_argument("--input", help="JSON file with product data")
    parser.add_argument("--output", default="prodcat.edi", help="Output EDI file")
    parser.add_argument("--sender", required=True, help="Sender ID")
    parser.add_argument("--receiver", required=True, help="Receiver ID")
    parser.add_argument("--config", help="Custom configuration file")
    parser.add_argument("--validate-only", action="store_true", help="Validate without generating")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--version", choices=['UNOA', 'UNOB', 'UNOC'], default='UNOA', 
                       help="EDIFACT syntax version")
    parser.add_argument("--split", type=int, help="Split into multiple files with max products per file")
    parser.add_argument("--streaming", action="store_true", help="Use streaming for large files")
    parser.add_argument("--force", action="store_true", help="Overwrite existing files")
    parser.add_argument("--verify", action="store_true", help="Verify generated EDI file")
    
    args = parser.parse_args()
    
    # Configure logging based on verbosity
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logger = configure_logging(log_level)
    
    try:
        # Initialize generator with custom config
        generator = EDIFACTGenerator(
            sender_id=args.sender,
            receiver_id=args.receiver,
            config_file=args.config,
            logger=logger
        )
        
        generator.set_edifact_version(args.version)
        
        # Load product data
        if args.input:
            with open(args.input, "r", encoding="utf-8") as f:
                products = json.load(f)
            logger.info("Loaded %d products from %s", len(products), args.input)
        else:
            # Sample data
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

        # Validate-only mode
        if args.validate_only:
            valid_count = sum(1 for p in products if generator.validate_product(p))
            print(f"\nValidation Results:")
            print(f"  Total products: {len(products)}")
            print(f"  Valid products: {valid_count}")
            print(f"  Invalid products: {len(products) - valid_count}")
            
            if generator.errors:
                print(f"\nValidation Errors:")
                for error in generator.errors[-10:]:  # Show last 10 errors
                    print(f"  - {error}")
            return

        # Generate EDI file(s)
        start_time = time.time()
        
        if args.split:
            created_files = generator.create_prodcat_files(products, args.split, args.output.replace('.edi', ''))
            if created_files:
                print(f"\n✓ Successfully created {len(created_files)} EDI files:")
                for file in created_files:
                    print(f"  - {file}")
                    
                    if args.verify:
                        verification = generator.verify_edi_file(file)
                        status = "✓ VALID" if verification['valid'] else "✗ INVALID"
                        print(f"    {status} - {verification['statistics']['total_segments']} segments")
            else:
                print("\n✗ Failed to create EDI files")
                for error in generator.errors:
                    print(f"  - {error}")
        else:
            success = generator.create_prodcat_file(
                products=products,
                filename=args.output,
                document_number="CATALOGUE-2024-001",
                sender_name="Tech Supplier Inc.",
                receiver_name="Electronics Retailer Ltd.",
                force=args.force,
                use_streaming=args.streaming
            )

            if success:
                print(f"\n✓ EDI PRODCAT file created successfully!")
                print(f"  File: {args.output}")
                
                if args.verify:
                    verification = generator.verify_edi_file(args.output)
                    status = "✓ VALID" if verification['valid'] else "✗ INVALID"
                    print(f"  Verification: {status}")
                    print(f"  Statistics: {verification['statistics']}")
                    
                    if verification['warnings']:
                        print(f"  Warnings: {verification['warnings']}")
            else:
                print("\n✗ Failed to create EDI PRODCAT file.")
                for error in generator.errors:
                    print(f"  - {error}")

        elapsed_time = time.time() - start_time
        print(f"\nExecution time: {elapsed_time:.2f} seconds")

    except (ValueError, json.JSONDecodeError, OSError) as e:
        logger.error("Error: %s", e)
        print(f"\n✗ Error: {e}")
        return 1
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
        return 1
    
    return 0

if __name__ == "__main__":
    exit(main())
