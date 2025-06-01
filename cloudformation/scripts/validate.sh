#!/bin/bash

set -e

echo "Validating CloudFormation templates..."

# Validate main template
aws cloudformation validate-template --template-body file://templates/main.yaml

# Validate nested templates
for template in templates/*.yaml; do
    if [ "$template" != "templates/main.yaml" ]; then
        echo "Validating $template..."
        aws cloudformation validate-template --template-body file://$template
    fi
done

echo "All templates are valid!"
