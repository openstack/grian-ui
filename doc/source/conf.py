#
# SPDX-License-Identifier: Apache-2.0

# Configuration file for the Sphinx documentation builder.
#
# For the full list of built-in configuration values, see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Project information -----------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#project-information

# project info
project = "grian-ui"
author = "OpenStack <openstack-discuss@lists.openstack.org>"

# The master toctree document.
master_doc = "index"

# General information about the project.
copyright = "2010-present, OpenStack Foundation"

# -- General configuration ---------------------------------------------------
# https://www.sphinx-doc.org/en/master/usage/configuration.html#general-configuration

extensions = [
    "openstackdocstheme",
]

exclude_patterns = []

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
add_module_names = False

# If true, sectionauthor and moduleauthor directives will be shown in the
# output. They are ignored by default.
show_authors = False

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "native"

# A list of ignored prefixes for module index sorting.
modindex_common_prefix = ["grian_ui."]

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
        "doc-grian-ui.tex",
        "Grian-UI Release Notes Documentation",
        "OpenStack <openstack-discuss@lists.openstack.org>",
        "manual",
    ),
]

latex_domain_indices = False

latex_elements = {
    "makeindex": "",
    "printindex": "",
    "preamble": r"\setcounter{tocdepth}{3}",
    "maxlistdepth": "10",
}

# Disable usage of xindy https://bugzilla.redhat.com/show_bug.cgi?id=1643664
latex_use_xindy = False

# Disable smartquotes, they don't work in latex
smartquotes_excludes = {"builders": ["latex"]}

# -- Options for openstackdocstheme ---------------------------------------

openstackdocs_repo_name = "openstack/grian-ui"
openstackdocs_bug_project = "grian-ui"
openstackdocs_bug_tag = ""
openstackdocs_pdf_link = True

# The suffix of source filenames.
source_suffix = ".rst"
