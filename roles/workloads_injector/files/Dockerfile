# Use the official RabbitMQ image with management plugin
FROM rabbitmq:3.12-management

# Expose default ports
# 5672: AMQP (default protocol)
# 1883: MQTT (if enabled)
# 15672: Management UI
EXPOSE 5672 1883 15672

# Set environment variables (optional)
ENV RABBITMQ_DEFAULT_USER=guest \
    RABBITMQ_DEFAULT_PASS=guest

# Enable MQTT plugin (optional, uncomment if needed)
# RUN rabbitmq-plugins enable --offline rabbitmq_mqtt

# Default command (inherited from base image)
CMD ["rabbitmq-server"]