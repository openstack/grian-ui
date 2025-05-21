# plugin.sh - DevStack plugin.sh dispatch script grian-ui

function install_grian_ui {
    setup_develop ${GRIAN_UI_DIR}
}

# check for services enabled
if is_service_enabled horizon && \
   is_service_enabled grian-ui; then
    if [[ "$1" == "stack" && "$2" == "pre-install" ]]; then
        # Set up system services
        # no-op
        :
    elif [[ "$1" == "stack" && "$2" == "install" ]]; then
        # Perform installation of service source
        echo_summary "Installing Grian UI"
        install_grian_ui
    elif [[ "$1" == "stack" && "$2" == "post-config" ]]; then
        # Configure after the other layer 1 and 2 services have been configured
        echo_summary "Configuring Grian UI"
        cp -a ${GRIAN_UI_DIR}/src/grian_ui/local/enabled/* ${DEST}/horizon/openstack_dashboard/local/enabled/
        cp -a ${GRIAN_UI_DIR}/src/grian_ui/local/local_settings.d/* ${DEST}/horizon/openstack_dashboard/local/local_settings.d/
    elif [[ "$1" == "stack" && "$2" == "extra" ]]; then
        # no-op
        :
    fi

    if [[ "$1" == "unstack" ]]; then
        # no-op
        :
    fi

    if [[ "$1" == "clean" ]]; then
        # Remove state and transient data
        # Remember clean.sh first calls unstack.sh
        # no-op
        :
    fi
fi
