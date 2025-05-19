#
# SPDX-License-Identifier: Apache-2.0

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

project = "grian-ui"
copyright = "2025, OpenStack <openstack-discuss@lists.openstack.org>"
author = "OpenStack <openstack-discuss@lists.openstack.org>"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "openstackdocstheme",
    "reno.sphinxext",
]

templates_path = ["_templates"]
exclude_patterns = []

# -- Options for Internationalization output ------------------------------
locale_dirs = ["locale/"]


# -- Options for HTML output -------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#options-for-html-output

html_theme = "openstackdocs"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".
html_static_path = ["_static"]

# If false, no index is generated.
html_use_index = False

# -- Options for LaTeX output ---------------------------------------------

# Grouping the document tree into LaTeX files. List of tuples
# (source start file, target name, title,
#  author, documentclass [howto, manual, or own class]).
latex_documents = [
    (
        "index",
        "GrianUIReleaseNotes.tex",
        "Grian-UI Release Notes Documentation",
        "OpenStack <openstack-discuss@lists.openstack.org>",
        "manual",
    ),
]

# -- Options for openstackdocstheme ---------------------------------------

openstackdocs_repo_name = "openstack/grian-ui"
openstackdocs_bug_project = "grian-ui"
openstackdocs_bug_tag = ""
