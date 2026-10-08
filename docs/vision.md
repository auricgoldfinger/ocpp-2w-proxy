# OCPP proxy for EV chargers
This project offers an OCPP proxy for EV chargers to be able to send information to different backends and receive commands from different backends. 

Responses on instructions sent from a backend must be returned to that backend only. Statistics and status messages initiated from the EV charger must be sent to all backends. It must be possible to configure allowed instructions for each backend to make sure multiple backends don't send conflicting instructions to the charger.

## Example use cases that could work simultaneously
* Allow an external backend to use roaming charging cards and handle financial administration and payments
* Allow a local backend like Home Assistant to modify charging speeds depending on the excess solar energy or low dynamic electricity prices
* Allow a local backend like Home Assistant to record statistics about current electricity usage, session times, etc